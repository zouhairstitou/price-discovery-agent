import base64
import json
from typing import Any, Dict, List
import numpy as np
import faiss
import google.auth
from google.auth.impersonated_credentials import Credentials as ImpersonatedCredentials
from googleapiclient.discovery import build
from google import genai
from google.genai import types
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import node

def fixed_rate(deal_value: float) -> tuple[str, float]:
    """Calculate the fees based on the 5% fixed rate."""
    fee_dollar = 0.05 * deal_value
    return "5%", fee_dollar

def tiered_rates(deal_value: float) -> tuple[str, float]:
    """Calculate the total fees based on 5% for first $50k and 2.5% for excess."""
    if deal_value <= 0:
        return "0.00%", 0.0
    tier_1_value = min(deal_value, 50000.0)
    tier_2_value = max(0.0, deal_value - 50000.0)
    fee_dollar = (0.05 * tier_1_value) + (0.025 * tier_2_value)
    fee_rate_percent = (fee_dollar / deal_value) * 100
    return f"{fee_rate_percent:.2f}%", fee_dollar

def parse_deal(ctx: Context, node_input: Any) -> Event:
    """Parses Pub/Sub JSON message and extracts deal fields, routing based on value.
    
    On HITL resume the Workflow reruns from START. If the deal is already stored
    in ctx.state (from the first pass), we skip re-parsing and re-route directly
    to avoid treating the user's text reply as a new deal event.
    """
    # Short-circuit on HITL resume: reuse the deal already parsed in the first pass.
    # If the user resumed via a plain text message, save their message text
    # into the deal's details so request_deal_details and subsequent nodes can use it.
    if ctx.state.get("deal"):
        existing_deal = ctx.state["deal"]
        raw_input: Any = None
        if hasattr(node_input, 'parts') and node_input.parts:
            raw_input = node_input.parts[0].text
        elif isinstance(node_input, str):
            raw_input = node_input
        elif isinstance(node_input, dict):
            raw_input = node_input.get("data", "")
            
        if raw_input and "serviceID" not in str(raw_input):
            existing_deal["deal_details"] = raw_input
            
        deal_value = existing_deal.get("deal_value", 0.0)
        route = "streamlined" if deal_value <= 100000.0 else "needs_input"
        return Event(output=existing_deal, route=route, state={"deal": existing_deal})
    
    raw_input = None
    if hasattr(node_input, 'parts') and node_input.parts:
        raw_input = node_input.parts[0].text
    elif isinstance(node_input, str):
        raw_input = node_input
    elif isinstance(node_input, dict):
        raw_input = node_input
        
    if isinstance(raw_input, str):
        try:
            payload = json.loads(raw_input)
        except Exception:
            payload = {"data": raw_input}
    else:
        payload = raw_input or {}
        
    data = payload.get("data", {})
    
    # Check for base64 encoded Pub/Sub data
    if isinstance(data, str):
        try:
            decoded = base64.b64decode(data).decode('utf-8')
            data = json.loads(decoded)
        except Exception:
            try:
                data = json.loads(data)
            except Exception:
                pass
                
    if not isinstance(data, dict):
        data = {}
        
    # Extract details
    serviceID = data.get("serviceID")
    date = data.get("date")
    service_description = data.get("service_description")
    
    # Parse numbers safely
    raw_val = data.get("deal_value", 0)
    if raw_val is None:
        raw_val = 0
    if isinstance(raw_val, str):
        try:
            raw_val = float(raw_val.replace('$', '').replace(',', '').strip())
        except ValueError:
            raw_val = 0.0
    try:
        deal_value = float(raw_val)
    except (TypeError, ValueError):
        deal_value = 0.0
    
    raw_fees = data.get("fees", 0)
    if raw_fees is None:
        raw_fees = 0
    if isinstance(raw_fees, str):
        try:
            raw_fees = float(raw_fees.replace('$', '').replace(',', '').strip())
        except ValueError:
            raw_fees = 0.0
    try:
        fees_val = float(raw_fees)
    except (TypeError, ValueError):
        fees_val = 0.0
    
    parsed_deal = {
        "serviceID": serviceID,
        "date": date,
        "service_description": service_description,
        "deal_value": deal_value,
        "fees": fees_val
    }
    
    # Determine the route based on deal_value
    if deal_value <= 100000.0:
        route = "streamlined"
    else:
        route = "needs_input"
        
    return Event(output=parsed_deal, route=route, state={"deal": parsed_deal})

def streamlined_fee(ctx: Context, node_input: Dict[str, Any]) -> Event:
    """Calculates fees automatically for Case_1 and Case_2."""
    deal_value = node_input.get("deal_value", 0.0)
    
    if deal_value <= 50000.0:
        fee_rate, fee_dollar = fixed_rate(deal_value)
        case_name = "Case 1 (Fixed Rate)"
    else:
        fee_rate, fee_dollar = tiered_rates(deal_value)
        case_name = "Case 2 (Tiered Rates)"
        
    output_message = (
        f"Ambient Price Discovery Agent processed deal.\n"
        f"Case: {case_name}\n"
        f"Deal Value: ${deal_value:,.2f}\n"
        f"Fee-rate: {fee_rate}\n"
        f"Fee$: ${fee_dollar:,.2f}"
    )
    
    content = types.Content(role='model', parts=[types.Part.from_text(text=output_message)])
    
    return Event(
        output={
            "case": case_name,
            "deal_value": deal_value,
            "fee_rate": fee_rate,
            "fee_amount": fee_dollar
        },
        content=content
    )

@node()
async def request_deal_details(ctx: Context, node_input: Dict[str, Any]):
    """Triggers human-in-the-loop pause for deals above $100k."""
    deal = ctx.state.get("deal", {})
    # If the user already provided the deal details (either via text resume
    # stored in parse_deal, or a prior turn), pass them through directly.
    if deal.get("deal_details"):
        yield Event(output=deal)
        return

    if not ctx.resume_inputs or "deal_details" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="deal_details",
            message="The deal value exceeds $100,000. Please provide the elaborated deal specification (or service description) to run similarity search."
        )
        return
        
    details = ctx.resume_inputs["deal_details"]
    deal["deal_details"] = details
    
    yield Event(output=deal)

def calculate_historical_fee(ctx: Context, node_input: Dict[str, Any]) -> Event:
    """Case 3: Fetch historical deals sheet, run FAISS vector search, and calculate average fee rates."""
    deal_value = node_input.get("deal_value", 0.0)
    deal_details = node_input.get("deal_details", "")
    
    # 1. Fetch data from Google Sheet using impersonation
    source_credentials, project = google.auth.default()
    target_service_account = 'price-discovery-agent@hybrid-life-501120-a5.iam.gserviceaccount.com'
    
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/cloud-platform'
    ]
    
    impersonated_creds = ImpersonatedCredentials(
        source_credentials=source_credentials,
        target_principal=target_service_account,
        target_scopes=scopes,
        lifetime=3600
    )
    
    service = build('sheets', 'v4', credentials=impersonated_creds, cache_discovery=False)
    spreadsheet_id = '136f5xem49Gj9yu1lVEDFa5a-gZxjw5_bTmUOLRJzrdc'
    
    spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_title = spreadsheet.get('sheets', [])[0].get('properties', {}).get('title', 'Sheet1')
    
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id, range=f"'{sheet_title}'!A:Z"
    ).execute()
    rows = result.get('values', [])
    
    if not rows or len(rows) < 2:
        raise ValueError("Google Sheet is empty or lacks headers.")
        
    header = rows[0]
    data_rows = rows[1:]
    
    # Identify indices case-insensitively
    header_lower = [h.lower().strip() for h in header]
    idx_desc = header_lower.index('service_description')
    idx_val = header_lower.index('deal_value')
    idx_fees = header_lower.index('fees')
    
    descriptions: List[str] = []
    deal_values: List[float] = []
    fees: List[float] = []
    
    for row in data_rows:
        if len(row) <= max(idx_desc, idx_val, idx_fees):
            continue
        try:
            val_str = row[idx_val].replace('$', '').replace(',', '').strip()
            fees_str = row[idx_fees].replace('$', '').replace(',', '').strip()
            val = float(val_str)
            fee = float(fees_str)
            descriptions.append(row[idx_desc])
            deal_values.append(val)
            fees.append(fee)
        except (ValueError, TypeError):
            # Skip rows with malformed numeric data
            continue

    if not descriptions:
        raise ValueError("No valid historical deal records found in the Google Sheet.")
        
    # 2. Embed historical descriptions and query details
    client = genai.Client(
        vertexai=True,
        project=project or 'hybrid-life-501120-a5',
        location='europe-west1',
        credentials=impersonated_creds
    )
    
    embed_response = client.models.embed_content(
        model="text-embedding-004",
        contents=descriptions
    )
    embeddings = [e.values for e in embed_response.embeddings]
    embeddings_np = np.array(embeddings).astype('float32')
    
    query_response = client.models.embed_content(
        model="text-embedding-004",
        contents=deal_details
    )
    query_emb = np.array([query_response.embeddings[0].values]).astype('float32')
    
    # 3. Create FAISS index and perform FlatL2 search
    dimension = embeddings_np.shape[1]
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings_np)
    
    k = min(5, len(descriptions))
    distances, indices = index.search(query_emb, k)
    
    top_5_fees: List[float] = []
    top_5_vals: List[float] = []
    similar_records_info: List[Dict[str, Any]] = []
    
    for rank, idx in enumerate(indices[0]):
        fee_val = fees[idx]
        d_val = deal_values[idx]
        top_5_fees.append(fee_val)
        top_5_vals.append(d_val)
        similar_records_info.append({
            "rank": rank + 1,
            "index": int(idx),
            "deal_value": d_val,
            "fee": fee_val,
            "distance": float(distances[0][rank]),
            "description": descriptions[idx][:100] + "..."
        })
        
    # 4. Perform averages and calculations
    avg_fee = float(np.mean(top_5_fees))
    avg_deal = float(np.mean(top_5_vals))
    if avg_deal > 0:
        target_fee = avg_fee / avg_deal
    else:
        target_fee = 0.05  # Default target rate of 5% if average deal value is 0
    calculated_fee = target_fee * deal_value
    
    output_message = (
        f"Ambient Price Discovery Agent processed deal.\n"
        f"Case: Case 3 (Historical FAISS Vector Search)\n"
        f"Deal Value: ${deal_value:,.2f}\n"
        f"Elaborated Spec: {deal_details}\n\n"
        f"Top {k} Similar Deals Found:\n"
    )
    for rec in similar_records_info:
        output_message += f"- Rank {rec['rank']}: Deal Val: ${rec['deal_value']:,.2f}, Fee: ${rec['fee']:,.2f} (Dist: {rec['distance']:.4f})\n"
        
    output_message += (
        f"\nCalculations:\n"
        f"Average Fee (avg_fee): ${avg_fee:,.2f}\n"
        f"Average Deal Value (avg_deal): ${avg_deal:,.2f}\n"
        f"Target Fee Rate (target_fee): {target_fee*100:.4f}%\n"
        f"Fee$: ${calculated_fee:,.2f}"
    )
    
    content = types.Content(role='model', parts=[types.Part.from_text(text=output_message)])
    
    return Event(
        output={
            "case": "Case 3 (Vector Search)",
            "deal_value": deal_value,
            "target_fee": target_fee,
            "fee_amount": calculated_fee,
            "similar_records": similar_records_info
        },
        content=content
    )
