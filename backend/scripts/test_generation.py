import requests

url = "http://localhost:8000/api/v1/cases/0f28fa8b-bc34-4749-a66b-70854bb7440d/generate-draft"
headers = {
    "Content-Type": "application/json",
    "X-User-Id": "15bdad68-a062-4c53-a564-2a96cc557f76"
}
payload = {}

print("Sending POST request to generate draft...")
response = requests.post(url, headers=headers, json=payload)
print(f"Status Code: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print("Guardrail Status:", data.get("guardrail_status"))
    print("Summary:", data.get("summary"))
    print("Missing/Uncertain:", data.get("missing_or_uncertain"))
    print("Claims:")
    for claim in data.get("claims", []):
        print(f" - {claim.get('claim')} | Source Refs: {claim.get('source_refs')}")
else:
    print("Response:", response.text)
