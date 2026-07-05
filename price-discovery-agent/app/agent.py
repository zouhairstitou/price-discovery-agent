# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from google.adk.workflow import Workflow
from google.adk.apps import App
from .tools import parse_deal, streamlined_fee, request_deal_details, calculate_historical_fee

# Define the Workflow graph structure compatible with ADK 2.0.
#
# Routing from parse_deal uses the dict-routing pattern:
#   (source, {route_value: target, ...})
# This matches how _process_chain handles conditional edges internally.
#
# Routes:
#   'streamlined' → streamlined_fee       (deal_value <= $100,000)
#   'needs_input' → request_deal_details  (deal_value > $100,000, HITL pause)
#
root_agent = Workflow(
    name="price_discovery_agent",
    description=(
        "Ambient Price Discovery Agent that captures reported brokerage deals "
        "and handles them in three different ways based on deal value."
    ),
    # rerun_on_resume=False: on HITL resume the workflow continues from the
    # paused node (request_deal_details) rather than restarting from START.
    rerun_on_resume=False,
    edges=[
        ('START', parse_deal),
        (parse_deal, {
            'streamlined': streamlined_fee,
            'needs_input': request_deal_details,
        }),
        (request_deal_details, calculate_historical_fee),
    ]
)

app = App(
    root_agent=root_agent,
    name="app",
)
