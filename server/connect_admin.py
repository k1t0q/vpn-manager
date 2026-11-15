"""Admin-panel proxy restricted to the allowlisted connection-page pilot."""
import os
from flask import jsonify, request, Response
import requests


def register(app, authorized):
    token = os.environ.get('CONNECT_ADMIN_TOKEN','')
    def proxy(path, data=None):
        if len(token) < 32:
            return jsonify(ok=False,error='Connection page pilot is not configured'),503
        headers={'X-Connect-Admin':token,'Idempotency-Key':request.headers.get('Idempotency-Key','')}
        try:
            if data is None:
                result=requests.get('http://127.0.0.1:5057'+path,headers=headers,timeout=5)
            else:
                result=requests.post('http://127.0.0.1:5057'+path,headers=headers,json=data,timeout=10)
        except requests.RequestException:
            return jsonify(ok=False,error='Connection page unavailable; retry the same request'),503
        return Response(result.content,status=result.status_code,content_type='application/json')

    @app.get('/api/connect/pilot')
    @authorized
    def connect_pilot():
        return proxy('/internal/status')

    @app.post('/api/connect/clients/<int:client_id>/link')
    @authorized
    def connect_link(client_id):
        data=request.get_json(silent=True)
        if not isinstance(data,dict) or set(data)!={'action'}:
            return jsonify(ok=False,error='Invalid link action'),400
        return proxy('/internal/link',dict(client_id=client_id,action=data['action']))
