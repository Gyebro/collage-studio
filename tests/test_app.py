"""API and pixel-accurate rendering smoke tests."""
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA_DIR', tmp_path)
    return TestClient(main.app)


def sample_image(color, size=(80, 100), fmt='PNG'):
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def test_default_project_has_65_cells(client):
    response = client.post('/api/projects', json={})
    assert response.status_code == 200
    project = response.json()
    assert project['showcase_bounds'] == [2, 2, 8, 8]
    assert project['expected_cells'] == 65
    assert project['filled_cells'] == 0
    assert len(main.expected_keys(project)) == 65


def test_upload_replace_crop_remove(client):
    project = client.post('/api/projects', json={}).json()
    url = f'/api/projects/{project["id"]}'
    body = sample_image('#ff0000')
    res = client.post(url+'/images', data={'cell':'r0c0'}, files={'file':('red.png',body,'image/png')})
    assert res.status_code == 200
    assert res.json()['filled_cells'] == 1
    tile = res.json()['tiles']['r0c0']
    preview = client.get(url+'/previews/'+tile['asset'].split('.')[0])
    assert preview.status_code == 200
    assert preview.headers['content-type'] == 'image/jpeg'
    t = client.patch(url+'/tiles/r0c0',json={'zoom':1.5,'pan_x':-.5,'pan_y':.8})
    assert t.status_code == 200
    assert t.json()['zoom'] == 1.5
    assert client.post(url+'/images',data={'cell':'r0c0'},files={'file':('blue.png',sample_image('blue'),'image/png')}).json()['filled_cells']==1
    assert client.delete(url+'/tiles/r0c0').json()['filled_cells']==0
    assert client.patch(url+'/tiles/r0c0',json={'zoom':1,'pan_x':0,'pan_y':0}).status_code==404


def test_covered_cell_rejected(client):
    p=client.post('/api/projects',json={}).json()
    res=client.post(f'/api/projects/{p["id"]}/images',data={'cell':'r4c4'},files={'file':('im.png',sample_image('blue'),'image/png')})
    assert res.status_code==400


def test_render_resolution_and_dpi_and_colors(client):
    p=client.post('/api/projects',json={}).json()
    url=f'/api/projects/{p["id"]}'
    client.post(url+'/images',data={'cell':'showcase'},files={'file':('hero.png',sample_image('red',(200,200)),'image/png')})
    client.post(url+'/images',data={'cell':'r0c0'},files={'file':('corner.png',sample_image('blue'),'image/png')})
    response=client.post(url+'/render',json={'width':800,'unit':'px','dpi':300,'quality':95})
    assert response.status_code==200
    assert response.headers['content-type']=='image/jpeg'
    img=Image.open(io.BytesIO(response.content))
    assert img.size==(800,1000)
    assert abs(img.info['dpi'][0]-300)<1
    assert img.getpixel((400,500))[0]>240  # showcase is red
    assert img.getpixel((20,20))[2]>220  # first small cell is blue
    assert all(190<v<255 for v in img.getpixel((120,20)))  # empty cells light gray


def test_download_zip_and_import(client):
    p=client.post('/api/projects',json={}).json()
    url=f'/api/projects/{p["id"]}'
    client.post(url+'/images',data={'cell':'showcase'},files={'file':('hero.png',sample_image('magenta'),'image/png')})
    client.patch(url+'/tiles/showcase',json={'zoom':2,'pan_x':.4,'pan_y':-.3})
    config=client.get(url+'/config.json')
    assert config.status_code==200
    assert config.json()['tiles']['showcase']['path'].startswith('images/')
    zipres=client.get(url+'/bundle.zip')
    assert zipres.status_code==200
    with zipfile.ZipFile(io.BytesIO(zipres.content)) as z:
        assert 'project.json' in z.namelist()
        assert len([n for n in z.namelist() if n.startswith('images/')])==1
    imported=client.post('/api/import-zip',files={'file':('collage-project.zip',zipres.content,'application/zip')})
    assert imported.status_code==200, imported.text
    ip=imported.json()
    assert ip['id']!=p['id']
    assert ip['filled_cells']==1
    assert ip['tiles']['showcase']['zoom']==2
    image=client.post(f'/api/projects/{ip["id"]}/render',json={'width':200,'unit':'px','dpi':72})
    assert image.status_code==200


def test_error_and_limits(client):
    assert client.post('/api/projects',json={'columns':4,'rows':4,'showcase':6}).status_code==422
    p=client.post('/api/projects',json={}).json()
    url=f'/api/projects/{p["id"]}'
    assert client.post(url+'/render',json={'width':14000,'unit':'px','dpi':300}).status_code==422
    bad=client.post(url+'/images',data={'cell':'r0c0'},files={'file':('bad.png',b'not an image','image/png')})
    assert bad.status_code==400
    assert client.post('/api/import-zip',files={'file':('bad.zip',b'abc','application/zip')}).status_code==400
