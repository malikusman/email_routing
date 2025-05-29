import requests
import json

class ZohoAPIClient:
    def __init__(self, credentials):
        self.access_token = credentials.token
        self.base_url = "https://mail.zoho.com/api/accounts/886337926/messages"  # Replace {account_id} with your Zoho account ID
        self.headers = {
            "Authorization": f"Zoho-oauthtoken {self.access_token}",
            "Content-Type": "application/json"
        }

    def list_messages(self, folder, unread):
        url = f"{self.base_url}?folder={folder}&unread={unread}"
        response = requests.get(url, headers=self.headers)
        return response.json() if response.status_code == 200 else {}

    def get_thread(self, thread_id):
        url = f"{self.base_url}/{thread_id}"
        response = requests.get(url, headers=self.headers)
        return response.json() if response.status_code == 200 else {}

    def create_draft(self, to_email, subject, raw_message, thread_id):
        url = f"{self.base_url}/drafts"
        payload = {
            "to": to_email,
            "subject": subject,
            "content": raw_message,
            "threadId": thread_id
        }
        response = requests.post(url, headers=self.headers, data=json.dumps(payload))
        return response.json() if response.status_code == 201 else {}

    def update_message(self, msg_id, updates):
        url = f"{self.base_url}/{msg_id}"
        response = requests.patch(url, headers=self.headers, data=json.dumps(updates))
        return response.status_code == 200