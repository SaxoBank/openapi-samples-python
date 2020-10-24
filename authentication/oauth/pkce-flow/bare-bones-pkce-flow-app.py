# tested in Python 3.6+
# required packages: flask, requests

import threading, secrets, webbrowser, requests, urllib, base64, hashlib

from time import sleep

from random import randint
from urllib.parse import urlparse
from pprint import pprint
from flask import Flask, request
from werkzeug.serving import make_server

app = Flask(__name__)

# copy your app configuration from https://www.developer.saxo/openapi/appmanagement
# make sure the redirect does NOT include a port (which is typical for PKCE flow)
app_conf = {
    "AppName": "Your app name",
    "AppKey": "Your app key",
    "AuthorizationEndpoint": "https://sim.logonvalidation.net/authorize",
    "TokenEndpoint": "https://sim.logonvalidation.net/token",
    "GrantType": "PKCE",
    "OpenApiBaseUrl": "https://gateway.saxobank.com/sim/openapi/",
    "RedirectUrls": ["http://your/redirect"],
}

# generate 10-character string as state
state = secrets.token_urlsafe(10)
port = str(randint(1000, 9999))  # randomly picked redirect URI port

# construct redirect url with random port number
# takes the first redirect url from the "RedirectUrls" array by default
r_url = urlparse(app_conf["RedirectUrls"][0])
ad_hoc_redirect = r_url.scheme + "://" + r_url.netloc + ":" + port + r_url.path


@app.route(r_url.path)
def handle_callback():
    """
    Saxo SSO will redirect to this endpoint after the user authenticates.
    """

    global received_callback, code, error_message, received_state
    error_message = None
    code = None

    if "error" in request.args:
        error_message = request.args["error"] + ": " + request.args["error_description"]
        render_text = "Error occurred. Please check the application command line."
    else:
        code = request.args["code"]
        render_text = "Please return to the application."

    received_state = request.args["state"]
    received_callback = True

    return render_text


class ServerThread(threading.Thread):
    """
    The Flask server will run inside a thread so it can be shut down when the callback is received.
    The server is automatically configured on the host and port specified in the app configuarion dictionary.
    """

    def __init__(self, app, redirect_url, port):
        threading.Thread.__init__(self)
        host = redirect_url.hostname
        port = port
        self.server = make_server(host, port, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        print("Starting server and listen for callback from Saxo...")
        self.server.serve_forever()

    def shutdown(self):
        print("Terminating server.")
        self.server.shutdown()


def code_verifier(n_bytes=64):
    """
    Function that encodes a 64-byte random string for use in URLs.
    """

    return base64.urlsafe_b64encode(secrets.token_bytes(n_bytes)).rstrip(b"=")


def code_challenge(verifier):
    """
    Function to generate code challenge based on RFC spec.
    See: https://tools.ietf.org/html/rfc7636#section-4.2
    """

    digest = hashlib.sha256(verifier).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=")


verifier = code_verifier()
challenge = code_challenge(verifier)

params = {
    "response_type": "code",
    "client_id": app_conf["AppKey"],
    "state": state,
    "redirect_uri": ad_hoc_redirect,
    "code_challenge": challenge,
    "code_challenge_method": "S256",
}

auth_url = requests.Request(
    "GET", url=app_conf["AuthorizationEndpoint"], params=params
).prepare()

print("Opening browser and loading authorization URL...")

received_callback = False
webbrowser.open_new(auth_url.url)

# after authentication in the browser, Saxo SSO will redirect to localhost server.
server = ServerThread(app, urlparse(ad_hoc_redirect), port)
server.start()
while not received_callback:
    try:
        sleep(0.1)
    except KeyboardInterrupt as e:
        print("Caught keyboard interrupt. Shutting down.")
        server.shutdown()
        exit(-1)
server.shutdown()

if state != received_state:
    print(
        "Received state does not match original state. Authentication possible compromised."
    )
    exit(-1)

if error_message:
    print("Received error message. Authentication not successful.")
    print(error_message)
    exit(-1)


print("Authentication successful. Requesting token...")

params = {
    "grant_type": "authorization_code",
    "code": code,
    "redirect_uri": ad_hoc_redirect,
    "client_id": app_conf["AppKey"],
    "code_verifier": verifier,
}

r = requests.post(app_conf["TokenEndpoint"], params=params)

print(r.url)

if r.status_code != 201:
    print("Error occurred while retrieving token. Terinating.")
    exit(-1)

print("Received token data:")
token_data = r.json()

pprint(token_data)


print("Requesting user data from OpenAPI...")

headers = {"Authorization": f"Bearer {token_data['access_token']}"}

r = requests.get(app_conf["OpenApiBaseUrl"] + "port/v1/users/me", headers=headers)

print(r.url)

if r.status_code != 200:
    print("Error occurred querying user data from the OpenAPI. Terminating.")

user_data = r.json()

pprint(user_data)


print("Using refresh token to obtain new token pair...")

params = {
    "grant_type": "refresh_token",
    "refresh_token": token_data["refresh_token"],
    "code_verifier": verifier,
}

r = requests.post(app_conf["TokenEndpoint"], params=params)

print(r.url)

if r.status_code != 201:
    print("Error occurred while retrieving token. Terinating.")
    exit(-1)

print("Received new token data:")
token_data = r.json()

pprint(token_data)
