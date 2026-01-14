"""
Web configuration endpoints for instance setup.

Provides a web UI for users to configure their Lichess account
and link it to an HLSS instance.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from hlss.config import get_settings
from hlss.database import get_db
from hlss.models import Instance, LichessAccount, ScreenType
from hlss.services.lichess import LichessService

router = APIRouter(prefix="/configure", tags=["configuration"])

DbSession = Annotated[Session, Depends(get_db)]
settings = get_settings()


def get_base_html(title: str, content: str) -> str:
    """Generate base HTML template."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - HLSS Configuration</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
            color: #e0e0e0;
        }}
        .container {{
            background: #1e1e2e;
            border-radius: 16px;
            padding: 40px;
            max-width: 480px;
            width: 100%;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
            border: 1px solid #2a2a3e;
        }}
        h1 {{
            color: #ffffff;
            margin-bottom: 8px;
            font-size: 24px;
        }}
        .subtitle {{
            color: #888;
            margin-bottom: 32px;
            font-size: 14px;
        }}
        .form-group {{
            margin-bottom: 24px;
        }}
        label {{
            display: block;
            margin-bottom: 8px;
            color: #b0b0b0;
            font-size: 14px;
            font-weight: 500;
        }}
        input[type="text"], input[type="password"] {{
            width: 100%;
            padding: 14px 16px;
            border: 2px solid #3a3a4e;
            border-radius: 8px;
            background: #12121a;
            color: #ffffff;
            font-size: 16px;
            transition: border-color 0.2s, box-shadow 0.2s;
        }}
        input[type="text"]:focus, input[type="password"]:focus {{
            outline: none;
            border-color: #6366f1;
            box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
        }}
        .help-text {{
            margin-top: 8px;
            font-size: 12px;
            color: #666;
        }}
        .help-text a {{
            color: #6366f1;
            text-decoration: none;
        }}
        .help-text a:hover {{
            text-decoration: underline;
        }}
        button {{
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: transform 0.1s, box-shadow 0.2s;
        }}
        button:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 20px rgba(99, 102, 241, 0.4);
        }}
        button:active {{
            transform: translateY(0);
        }}
        .error {{
            background: #2a1a1a;
            border: 1px solid #dc2626;
            color: #ef4444;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 14px;
        }}
        .success {{
            background: #1a2a1a;
            border: 1px solid #16a34a;
            color: #22c55e;
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 24px;
            font-size: 14px;
        }}
        .success-container {{
            text-align: center;
        }}
        .success-icon {{
            font-size: 64px;
            margin-bottom: 24px;
        }}
        .chess-icon {{
            display: inline-block;
            margin-right: 8px;
        }}
        .instance-id {{
            font-family: monospace;
            background: #12121a;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #888;
        }}
    </style>
</head>
<body>
    <div class="container">
        {content}
    </div>
</body>
</html>"""


@router.get("/{instance_id}", response_class=HTMLResponse)
def show_configuration_form(instance_id: str, db: DbSession, error: str = None) -> str:
    """Show the configuration form for an instance."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    error_html = ""
    if error:
        error_html = f'<div class="error">{error}</div>'

    content = f"""
        <h1><span class="chess-icon">♟</span>Configure Lichess</h1>
        <p class="subtitle">Link your Lichess account to your e-Ink device</p>
        
        {error_html}
        
        <form method="POST" action="/configure/{instance_id}">
            <div class="form-group">
                <label for="api_token">Lichess API Token</label>
                <input type="password" id="api_token" name="api_token" 
                       placeholder="lip_xxxxxxxxxxxxxxxx" required>
                <p class="help-text">
                    Get your token from 
                    <a href="https://lichess.org/account/oauth/token" target="_blank">
                        lichess.org/account/oauth/token
                    </a>
                    <br>Required scopes: <strong>board:play</strong>, <strong>challenge:read</strong>, <strong>challenge:write</strong>
                </p>
            </div>
            
            <button type="submit">Connect to Lichess</button>
        </form>
        
        <p class="help-text" style="margin-top: 24px; text-align: center;">
            Instance: <span class="instance-id">{instance_id[:8]}...</span>
        </p>
    """

    return get_base_html("Connect", content)


@router.post("/{instance_id}", response_class=HTMLResponse)
def process_configuration(
    instance_id: str,
    db: DbSession,
    api_token: str = Form(...),
) -> HTMLResponse:
    """Process the configuration form submission."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    # Validate the API token by fetching the user profile from Lichess
    lichess_service = LichessService(api_token)

    try:
        user_info = lichess_service.get_account()
        username = user_info.get("username")

        if not username:
            return RedirectResponse(
                url=f"/configure/{instance_id}?error=Invalid+API+token",
                status_code=status.HTTP_303_SEE_OTHER,
            )
    except Exception as e:
        return RedirectResponse(
            url=f"/configure/{instance_id}?error=Could+not+verify+token:+{str(e)[:50]}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Check if this username already has an account
    existing_account = db.scalar(select(LichessAccount).where(LichessAccount.username == username))

    if existing_account:
        # Update the existing account's token
        existing_account.api_token = api_token
        existing_account.is_enabled = True
        account = existing_account
    else:
        # Create a new account
        account = LichessAccount(
            username=username,
            api_token=api_token,
            is_enabled=True,
            is_default=True,  # First account is default
        )
        db.add(account)

    # Link the account to the instance
    instance.linked_account_id = account.id
    instance.is_ready = True
    instance.needs_configuration = False
    instance.current_screen = ScreenType.NEW_MATCH

    db.commit()

    # Show success page
    content = f"""
        <div class="success-container">
            <div class="success-icon">✓</div>
            <h1>Successfully Connected!</h1>
            <p class="subtitle">Your e-Ink device is now linked to Lichess</p>
            
            <div class="success" style="text-align: left; margin-top: 24px;">
                <strong>Account:</strong> {username}<br>
                <strong>Status:</strong> Ready to play
            </div>
            
            <p class="help-text" style="margin-top: 24px;">
                You can close this page and return to your device.<br>
                The display will update automatically.
            </p>
        </div>
    """

    return HTMLResponse(content=get_base_html("Success", content))


@router.get("/{instance_id}/status")
def get_configuration_status(instance_id: str, db: DbSession) -> dict:
    """Get the configuration status of an instance (for polling)."""
    instance = db.get(Instance, instance_id)
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Instance not found",
        )

    account = None
    if instance.linked_account_id:
        account = db.get(LichessAccount, instance.linked_account_id)

    return {
        "instance_id": instance_id,
        "is_configured": not instance.needs_configuration,
        "is_ready": instance.is_ready,
        "linked_account": account.username if account else None,
        "current_screen": instance.current_screen.value if instance.current_screen else None,
    }
