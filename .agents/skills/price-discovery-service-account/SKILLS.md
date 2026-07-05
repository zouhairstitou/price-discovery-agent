---
name: Service-account-creator
description: use this skill to create a service account, to grant the agent rights to view my google sheet.
---

# Create a Service Account
This skill creates a google service account for the agent to identify itself on google clound and to get access to the google sheets. The access is mainly for reading columns of interest.
The environment variables are passed in by the prompt writer as a dictionary, or in natural language pairing the keys with their significant values.
 
## Instructions
1. **Set the environment variables*:
PROJECT_ID=<YOUR-PROJECT-ID>
REGION=<YOUR_REGION>
SPREADSHEET_ID=<YOUR_SPREADSHEET_ID>
SA_NAME="price-discovery-agent"
SERVICE_ACCOUNT_EMAIL=$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com

2.**Enable Required Google Cloud APIs**
gcloud services enable \
  sheets.googleapis.com \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  logging.googleapis.com \
  --project=$PROJECT_ID

3.**Create Service Account**
gcloud iam service-accounts create $SA_NAME \
    --description="Service account for price discovery agent" \
    --display-name="price discovery Agent Service Account" \
    --project=$PROJECT_ID

4.**Grant the Vertex AI User role to the Service Account**
gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:$SERVICE_ACCOUNT_EMAIL" \
    --role="roles/aiplatform.user"

5.**Grant your gcloud identity permission to impersonate the Service Account**
USER_EMAIL=$(gcloud config get-value account)
gcloud iam service-accounts add-iam-policy-binding $SERVICE_ACCOUNT_EMAIL \
    --member="user:$USER_EMAIL" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project=$PROJECT_ID