from __future__ import annotations

import msal
import requests
from flask import current_app

from app.logging import logger

_GRAPH_TOKEN_URL = 'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=current_app.config['MS_AUTH_CLIENT_ID'],
        client_credential=current_app.config['MS_AUTH_CLIENT_SECRET'],
        authority=current_app.config['MS_AUTH_AUTHORITY'],
    )


def build_auth_flow(redirect_uri: str) -> dict:
    """Starts the interactive OAuth flow. Caller must stash the returned dict
    in flask.session['ms_flow'] before redirecting the browser to flow['auth_uri']."""
    return _msal_app().initiate_auth_code_flow(scopes=[], redirect_uri=redirect_uri)


def complete_auth_flow(flow: dict, auth_response: dict) -> dict:
    """Exchanges the callback's query params for tokens. Returns the msal result
    dict — check for an 'error' key before trusting 'id_token_claims'."""
    return _msal_app().acquire_token_by_auth_code_flow(flow, auth_response)


def _graph_app_token() -> str:
    """App-only client-credentials token — same pattern as email_service._get_access_token()."""
    resp = requests.post(
        _GRAPH_TOKEN_URL.format(tenant_id=current_app.config['MS_AUTH_TENANT_ID']),
        data={
            'grant_type':    'client_credentials',
            'client_id':     current_app.config['MS_AUTH_CLIENT_ID'],
            'client_secret': current_app.config['MS_AUTH_CLIENT_SECRET'],
            'scope':         'https://graph.microsoft.com/.default',
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()['access_token']


def get_profile_details(user_oid: str) -> dict:
    """Job title / department for the header profile card. Uses the same
    app-only Graph token as has_admin_app_role -- no delegated user scope
    needed since User.Read.All is already granted to this app."""
    token = _graph_app_token()
    resp = requests.get(
        f'https://graph.microsoft.com/v1.0/users/{user_oid}',
        params={'$select': 'jobTitle,department'},
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return {'job_title': data.get('jobTitle'), 'department': data.get('department')}


def get_profile_photo(user_oid: str) -> tuple[bytes, str] | None:
    """Returns (photo_bytes, content_type), or None if the user has no photo
    set in Azure AD (Graph returns 404 in that case -- not an error)."""
    token = _graph_app_token()
    resp = requests.get(
        f'https://graph.microsoft.com/v1.0/users/{user_oid}/photos/64x64/$value',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content, resp.headers.get('Content-Type', 'image/jpeg')


def has_admin_app_role(user_oid: str) -> bool:
    """Checks whether user_oid holds the ia-support-admin App Role on the
    configured service principal. A Graph failure raises rather than
    silently granting access — callers must treat exceptions as denied."""
    sp_id = current_app.config['MS_AUTH_SP_ID']
    role_id = current_app.config['MS_AUTH_ADMIN_ROLE_ID']
    token = _graph_app_token()
    resp = requests.get(
        f'https://graph.microsoft.com/v1.0/servicePrincipals/{sp_id}/appRoleAssignedTo',
        headers={'Authorization': f'Bearer {token}'},
        timeout=15,
    )
    resp.raise_for_status()
    assignments = resp.json().get('value', [])
    matched = any(a.get('principalId') == user_oid and a.get('appRoleId') == role_id for a in assignments)
    if not matched:
        logger.info('ms_auth_role_denied', oid=user_oid)
    return matched
