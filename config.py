import os

BASE_URL = "https://api.siperb.com"
TRIAL_HOURS = 1
TRIAL_MINUTES = 15
EXPIRING_SOON_DAYS = 3
REGISTRAR_PORT = 5060
TRANSPORT_TYPE = "udp"
SBC_HOST = "eu-west-1-sbc-1.siperb.com"
PBX_PASSWORD = "Office@11"

# All secrets loaded from environment variables (Streamlit Cloud sets these from Secrets)
SIPERB_PAT = os.environ.get("SIPERB_PAT")
SIPERB_PAT_ENCODED = os.environ.get("SIPERB_PAT_ENCODED")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
CONNEXCS_USERNAME = os.environ.get("CONNEXCS_USERNAME")
CONNEXCS_PASSWORD = os.environ.get("CONNEXCS_PASSWORD")
