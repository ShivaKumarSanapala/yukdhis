"""Create a Vertex AI Agent Engine instance to host ADK's Memory Bank; prints its id."""

import os

import vertexai

client = vertexai.Client(project=os.environ["GOOGLE_CLOUD_PROJECT"], location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
engine = client.agent_engines.create(config={"display_name": "yukdhis-memory-bank"})
print(engine.api_resource.name.split("/")[-1])
