import secrets
import string
from config import REGISTRAR_PORT, TRANSPORT_TYPE, SBC_HOST, PBX_PASSWORD

def random_password(length=16):
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))

def build_connexcs_profile(caller_id):
    return {
        "label": "ConnexCS",
        "connection_name": "connexcs",
        "connection_type": "OutboundTrunk",
        "description": caller_id,
        "registrar_address": "162.243.163.173",
        "username": caller_id,
        "registrar_username": caller_id,
        "password": random_password(),
    }

def build_pbx_profile(domain, extension, did):
    return {
        "label": "PBX",
        "connection_name": "FSPBX",
        "connection_type": "OutboundRegistration",
        "description": did,
        "registrar_address": domain,
        "username": extension,
        "registrar_username": extension,
        "password": PBX_PASSWORD,
    }

def build_connection_payload(profile, expiry_time):
    return {
        "Name": profile["connection_name"],
        "Type": profile["connection_type"],
        "DialPattern": ".",
        "Weight": "0",
        "Disabled": False,
        "AllowSrc": ["0.0.0.0/0"],
        "CustomHeaders": [],
        "OutboundCallConcurrency": 1,
        "Expiry": {"End": expiry_time.isoformat().replace("+00:00", "Z")},
        "Registration": {
            "registrar": "",
            "registrar_address": profile["registrar_address"],
            "registrar_port": REGISTRAR_PORT,
            "transport_type": TRANSPORT_TYPE,
            "transport_port": REGISTRAR_PORT,
            "realm": "*",
            "username": profile["username"],
            "registrar_username": profile["registrar_username"],
            "password": profile["password"]
        },
        "SbcServer": {"Host": SBC_HOST},
        "Transcoding": {"ServerLiveIp": "0.0.0.0", "Enabled": True, "Options": {}},
        "TimeOfDayRouting": None
    }
