from fastapi.testclient import TestClient
import backend.main as m
client=TestClient(m.app)
def test_health():assert client.get('/api/v1/health').json()['status']=='ok'
def test_no_secrets_in_provider_list(monkeypatch):
 monkeypatch.setitem(m.P['openai'],'key','top-secret');data=client.get('/api/v1/providers').text;assert 'top-secret' not in data and 'key' not in data
