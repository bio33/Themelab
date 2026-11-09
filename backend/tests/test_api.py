import io
from fastapi.testclient import TestClient
from PIL import Image
from app import main


def test_theme_creation_and_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'THEMES_ROOT', tmp_path)
    client = TestClient(main.app)
    creation = client.post('/api/themes', json={'name': 'Neon City'})
    assert creation.status_code == 201
    theme_id = creation.json()['id']
    img = Image.new('RGB', (360, 780), color='#3a2b7a')
    stream = io.BytesIO()
    img.save(stream, 'PNG')
    response = client.post(f'/api/themes/{theme_id}/wallpaper', files={'file': ('wallpaper.png', stream.getvalue(), 'image/png')})
    assert response.status_code == 200
    assert response.json()['wallpaper']['height'] == 780
    assert (tmp_path / theme_id / 'wallpaper.png').exists()
    assert client.get(f'/api/themes/{theme_id}/wallpaper').status_code == 200


def test_theme_path_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'THEMES_ROOT', tmp_path)
    assert TestClient(main.app).get('/api/themes/invalid/wallpaper').status_code == 404
