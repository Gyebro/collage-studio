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


def test_uniform_photo_borders_and_no_outer_border(client):
    p = client.post('/api/projects', json={}).json()
    url = f'/api/projects/{p["id"]}'
    assert p['border_percent'] == 0
    assert p['border_color'] == '#ffffff'
    assert client.patch(url+'/appearance', json={
        'border_percent': 10, 'border_color': '#11cc33'
    }).status_code == 200
    assert client.patch(url+'/appearance', json={
        'border_percent': 31, 'border_color': '#11cc33'
    }).status_code == 422
    assert client.patch(url+'/appearance', json={
        'border_percent': 10, 'border_color': 'green'
    }).status_code == 422
    client.post(url+'/images', data={'cell': 'r0c0'}, files={
        'file': ('corner.png', sample_image('red'), 'image/png')
    })
    client.post(url+'/images', data={'cell': 'showcase'}, files={
        'file': ('hero.png', sample_image('blue'), 'image/png')
    })
    resp = client.post(url+'/render', json={'width': 400, 'unit': 'px', 'dpi': 300, 'quality': 100})
    assert resp.status_code == 200
    im = Image.open(io.BytesIO(resp.content))
    assert im.size == (400, 500)
    # Grid cell size is 40x50, gap is 10% of 40 => exactly 4 pixels.
    # Between the first and second tiles: pixels 38,39,40,41 are green.
    for x in range(38,42):
        r,g,b = im.getpixel((x,25))
        assert g > 180 and r < 60 and b < 90, (x, (r,g,b))
    r,g,b = im.getpixel((37,25))
    assert r > 225 and g < 45
    assert im.getpixel((0,25))[0] > 225  # no outer border
    assert im.getpixel((20,0))[0] > 225
    # Between small cells and the showcase at x=80 / y=125.
    for x in range(78,82):
        r,g,b = im.getpixel((x,125))
        assert g > 180 and r < 60 and b < 90, (x, (r,g,b))
    assert im.getpixel((84,125))[2] > 210  # showcase remains blue
    for y in range(48,52):
        r,g,b = im.getpixel((20,y))
        assert g > 180 and r < 60 and b < 90, (y, (r,g,b))
    cfg = client.get(url+'/config.json').json()
    assert cfg['border_percent'] == 10
    assert cfg['border_color'] == '#11cc33'
    z = client.get(url+'/bundle.zip')
    imported = client.post('/api/import-zip', files={'file':('copy.zip',z.content,'application/zip')})
    assert imported.status_code == 200, imported.text
    assert imported.json()['border_percent'] == 10
    assert imported.json()['border_color'] == '#11cc33'


def test_exact_duplicate_diagnostic_upload_replace_remove_and_import(client):
    p = client.post('/api/projects', json={}).json()
    url = f'/api/projects/{p["id"]}'
    body = sample_image('#dc1478')
    res = client.post(url+'/images', data={'cell':'r0c0'},files={'file':('a.png',body,'image/png')})
    assert res.json()['duplicate_repeats'] == 0
    res = client.post(url+'/images', data={'cell':'r0c1'},files={'file':('different-name.png',body,'image/png')})
    dup = res.json()
    assert dup['duplicate_repeats'] == 1
    assert len(dup['duplicate_groups']) == 1
    assert dup['duplicate_groups'][0]['cells'] == ['r0c0', 'r0c1']
    assert dup['duplicate_groups'][0]['filenames'] == ['a.png', 'different-name.png']
    res = client.post(url+'/images', data={'cell':'showcase'},files={'file':('a.png',body,'image/png')})
    assert res.json()['duplicate_repeats'] == 2
    assert len(res.json()['duplicate_groups'][0]['cells']) == 3
    assert res.json()['tiles']['r0c0']['asset'] != res.json()['tiles']['r0c1']['asset']
    other = sample_image('#0000ff')
    res = client.post(url+'/images', data={'cell':'r0c1'},files={'file':('a.png',other,'image/png')})
    assert res.json()['duplicate_repeats'] == 1  # matching filename doesn't matter
    assert res.json()['duplicate_groups'][0]['cells'] == ['r0c0','showcase']
    zipres = client.get(url+'/bundle.zip')
    imported = client.post('/api/import-zip',files={'file':('export.zip',zipres.content,'application/zip')})
    assert imported.status_code == 200
    assert imported.json()['duplicate_repeats'] == 1
    assert client.delete(url+'/tiles/showcase').json()['duplicate_repeats'] == 0


def test_legacy_zip_import_falls_back_to_edge_to_edge_and_rehashes(client):
    p = client.post('/api/projects', json={}).json()
    url = f'/api/projects/{p["id"]}'
    body = sample_image('red')
    for cell in ('r0c0','r0c1'):
        client.post(url+'/images',data={'cell':cell},files={'file':('same.png',body,'image/png')})
    zipres=client.get(url+'/bundle.zip')
    old=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(zipres.content)) as archive, zipfile.ZipFile(old,'w') as legacy:
        for entry in archive.infolist():
            contents=archive.read(entry.filename)
            if entry.filename=='project.json':
                cfg=json.loads(contents)
                del cfg['border_percent']
                del cfg['border_color']
                for tile in cfg['tiles'].values():
                    del tile['sha256']
                contents=json.dumps(cfg).encode()
            legacy.writestr(entry.filename,contents)
    restored=client.post('/api/import-zip',files={'file':('legacy.zip',old.getvalue(),'application/zip')})
    assert restored.status_code == 200, restored.text
    assert restored.json()['border_percent'] == 0
    assert restored.json()['border_color'] == '#ffffff'
    assert restored.json()['duplicate_repeats'] == 1
    assert all(len(tile['sha256'])==64 for tile in restored.json()['tiles'].values())
