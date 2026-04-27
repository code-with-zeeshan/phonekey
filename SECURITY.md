# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 3.2.x | ✅ Active |
| 3.1.x | ⚠️ Security fixes only |
| < 3.1 | ❌ End of life |

## Security Model

PhoneKey is a **local-network tool** designed to be run on your own laptop.

| Protection | How |
|---|---|
| Connection PIN | 4-digit PIN prevents unauthorised phones from connecting |
| TLS (optional) | `--https` encrypts HTTP and WebSocket traffic on LAN |
| Cloudflare Tunnel | `--tunnel` provides trusted HTTPS without certificate warnings |
| Tab deduplication | Prevents phantom sessions from stale browser tabs |
| No persistent storage | PIN, device list, and session state exist only in memory |
| Environment guard | Refuses to start in detected cloud/CI environments |

## Threat Model

PhoneKey is **not** designed for:
- Multi-tenant or shared-machine environments
- Exposure to the public internet without `--tunnel`
- Replacing enterprise remote-desktop security controls

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Email: your-email@example.com  
Subject: `[PhoneKey Security] Brief description`

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Your suggested fix (optional)

You will receive a response within 72 hours. If the issue is confirmed,
a patch will be released within 14 days and you will be credited in the
changelog (unless you prefer to remain anonymous).