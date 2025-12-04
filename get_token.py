from google_auth_oauthlib.flow import InstalledAppFlow

# Scopes needed for Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']

def main():
    flow = InstalledAppFlow.from_client_secrets_file(
        'client_secrets.json', SCOPES)

    creds = flow.run_local_server(port=0)

    print("\n\n--- COPY THESE VALUES FOR RENDER ---")
    print(f"REFRESH_TOKEN: {creds.refresh_token}")
    print(f"CLIENT_ID: {creds.client_id}")
    print(f"CLIENT_SECRET: {creds.client_secret}")

if __name__ == '__main__':
    main()
