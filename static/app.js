/* Collage Studio — dependency-free browser editor. Original images stay in the local Python server. */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const canvas = $('collageCanvas');
  const shell = $('canvasShell');
  const ctx = canvas.getContext('2d', { alpha: false });
  const state = {
    project: null,
    selected: null,
    hovered: null,
    dropTarget: null,
    fineTune: false,
    showGrid: true,
    imageCache: new Map(),
    targetUpload: null,
    pointerDrag: null,
    pendingPatches: new Map(),
    patchSequence: Promise.resolve(),
    patchGeneration: 0,
    toastTimer: null,
    busy: false,
  };
  const LIMIT_PIXELS = 80000000;

  function safeText(value) { return String(value ?? ''); }
  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
  function niceNumber(value) { return Math.round(value).toLocaleString('en-US'); }
  function bounds(p) {
    const x0 = Math.floor((p.columns - p.showcase) / 2);
    const y0 = Math.floor((p.rows - p.showcase) / 2);
    return [x0, y0, x0 + p.showcase, y0 + p.showcase];
  }
  function expectedCells(p) { return p.columns * p.rows - p.showcase * p.showcase + 1; }
  function cells(p) {
    const [x0,y0,x1,y1] = bounds(p);
    const result = [];
    for (let r=0;r<p.rows;r++) for (let c=0;c<p.columns;c++) {
      if (c < x0 || c >= x1 || r < y0 || r >= y1) result.push(`r${r}c${c}`);
    }
    result.push('showcase');
    return result;
  }
  function rectFor(key, p, w, h) {
    if (key === 'showcase') {
      const [x0,y0,x1,y1] = bounds(p);
      return { x: x0*w/p.columns, y: y0*h/p.rows, w: (x1-x0)*w/p.columns, h:(y1-y0)*h/p.rows };
    }
    const parts = /^r(\d+)c(\d+)$/.exec(key);
    if (!parts) return null;
    const r = +parts[1], c = +parts[2];
    return { x:c*w/p.columns, y:r*h/p.rows, w:w/p.columns, h:h/p.rows };
  }
  function cellFromPoint(clientX, clientY) {
    const p = state.project;
    if (!p) return null;
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width;
    const y = (clientY - rect.top) / rect.height;
    if (x < 0 || x >= 1 || y < 0 || y >= 1) return null;
    const c = Math.floor(x*p.columns), r = Math.floor(y*p.rows);
    const [x0,y0,x1,y1] = bounds(p);
    return (c >= x0 && c < x1 && r >= y0 && r < y1) ? 'showcase' : `r${r}c${c}`;
  }
  function cellName(key) {
    if (key === 'showcase') return 'Center showcase';
    const parts = /^r(\d+)c(\d+)$/.exec(key);
    return parts ? `Row ${+parts[1]+1}, column ${+parts[2]+1}` : 'Photo';
  }
  function previewUrl(tile) {
    const id = tile.asset.split('.')[0];
    return `/api/projects/${state.project.id}/previews/${id}`;
  }
  function getImage(tile) {
    const cacheKey = `${state.project.id}/${tile.asset}`;
    if (state.imageCache.has(cacheKey)) return state.imageCache.get(cacheKey);
    const image = new Image();
    image.decoding = 'async';
    image.onload = draw;
    image.onerror = () => { image.failed = true; draw(); };
    image.src = previewUrl(tile);
    state.imageCache.set(cacheKey, image);
    return image;
  }
  function imageMetrics(img, tile, rect) {
    const zoom = tile.zoom ?? 1;
    const factor = Math.max(rect.w/img.naturalWidth, rect.h/img.naturalHeight)*zoom;
    const w = img.naturalWidth*factor, h = img.naturalHeight*factor;
    const ox = Math.max(0, w-rect.w), oy = Math.max(0, h-rect.h);
    return { w, h, ox, oy,
      x: rect.x - ox*((tile.pan_x ?? 0)+1)/2,
      y: rect.y - oy*((tile.pan_y ?? 0)+1)/2 };
  }
  function drawPlaceholder(key, rect) {
    const isHero = key === 'showcase';
    ctx.fillStyle = isHero ? '#eae7fb' : '#f0eff6';
    ctx.fillRect(rect.x,rect.y,rect.w,rect.h);
    const gap = isHero ? Math.min(13,rect.w/20) : Math.min(4, rect.w/10);
    if (rect.w > 12 && rect.h > 12) {
      ctx.save();
      ctx.strokeStyle = isHero ? '#b8b0ec' : '#dedce9';
      ctx.lineWidth = isHero ? 1.4 : .65;
      ctx.setLineDash(isHero ? [6,5] : [2,3]);
      ctx.strokeRect(rect.x+gap,rect.y+gap,rect.w-2*gap,rect.h-2*gap);
      ctx.restore();
    }
    if (isHero && rect.w > 105 && rect.h > 100) {
      const centerX = rect.x+rect.w/2, centerY=rect.y+rect.h/2;
      const iconSize = clamp(rect.w/7,32,53);
      ctx.save();
      ctx.strokeStyle = '#8c80e7';
      ctx.lineWidth = 2;
      ctx.lineCap = 'round';
      ctx.strokeRect(centerX-iconSize*.47,centerY-iconSize*.90,iconSize*.94,iconSize*.78);
      ctx.beginPath();
      ctx.arc(centerX+iconSize*.17, centerY-iconSize*.65, iconSize*.075, 0, Math.PI*2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(centerX-iconSize*.40,centerY-iconSize*.20);
      ctx.lineTo(centerX-iconSize*.11,centerY-iconSize*.53);
      ctx.lineTo(centerX+iconSize*.09,centerY-iconSize*.36);
      ctx.lineTo(centerX+iconSize*.23,centerY-iconSize*.46);
      ctx.lineTo(centerX+iconSize*.40,centerY-iconSize*.23);
      ctx.stroke();
      const size = clamp(rect.w/24,11,15);
      ctx.font = `800 ${size}px Inter, system-ui, sans-serif`;
      ctx.fillStyle = '#7b70cb';
      ctx.textAlign = 'center';
      ctx.fillText('YOUR SHOWCASE PHOTO',centerX,centerY+iconSize*.36);
      ctx.font = `500 ${clamp(size*.79,9,12)}px Inter, system-ui, sans-serif`;
      ctx.fillStyle = '#9d97c5';
      ctx.fillText('Click to add your hero image',centerX,centerY+iconSize*.72);
      ctx.restore();
    }
  }
  function drawTile(key,w,h) {
    const p = state.project;
    const rect = rectFor(key,p,w,h);
    if (!rect) return;
    const tile = p.tiles[key];
    if (!tile) drawPlaceholder(key,rect);
    else {
      const image = getImage(tile);
      if (image.complete && image.naturalWidth > 0) {
        const m = imageMetrics(image,tile,rect);
        ctx.save();
        ctx.beginPath();ctx.rect(rect.x,rect.y,rect.w,rect.h);ctx.clip();
        ctx.drawImage(image,m.x,m.y,m.w,m.h);
        ctx.restore();
      } else {
        ctx.fillStyle = '#e9e7ef';ctx.fillRect(rect.x,rect.y,rect.w,rect.h);
      }
    }
    if (state.showGrid) {
      ctx.strokeStyle = '#ffffffdb'; ctx.lineWidth = 1.1;
      ctx.strokeRect(rect.x+.55,rect.y+.55,Math.max(0,rect.w-1.1),Math.max(0,rect.h-1.1));
    }
  }
  function drawHighlight(key,color) {
    const rect = rectFor(key,state.project,canvas.clientWidth,canvas.clientHeight);
    if (!rect) return;
    ctx.save();
    ctx.fillStyle = color === 'drop' ? '#5347ed40' : '#5c50e621';
    ctx.fillRect(rect.x,rect.y,rect.w,rect.h);
    ctx.strokeStyle = color === 'drop' ? '#4435d9' : '#584ef2';
    ctx.lineWidth = rect.w < 20 ? 1.6 : 2.5;
    ctx.strokeRect(rect.x+1.5,rect.y+1.5,rect.w-3,rect.h-3);
    ctx.restore();
  }
  function draw() {
    if (!state.project || !canvas.clientWidth || !canvas.clientHeight) return;
    const w=canvas.clientWidth,h=canvas.clientHeight;
    const dpr=Math.min(window.devicePixelRatio||1,2);
    const bw=Math.round(w*dpr),bh=Math.round(h*dpr);
    if (canvas.width !== bw || canvas.height !== bh) {canvas.width=bw;canvas.height=bh;}
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.fillStyle='#eeedf3';ctx.fillRect(0,0,w,h);
    for (const key of cells(state.project)) drawTile(key,w,h);
    if (state.fineTune && state.selected) drawHighlight(state.selected,'selected');
    else if (state.hovered && !state.fineTune) drawHighlight(state.hovered,'hovered');
    if (state.dropTarget) drawHighlight(state.dropTarget,'drop');
  }

  function saveStatus(status) { $('saveStatus').textContent = status; }
  function toast(message, error=false) {
    const el = $('toast');
    el.textContent = message;
    el.className = 'toast show' + (error ? ' error' : '');
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => el.className = 'toast', 3600);
  }
  async function request(url, options={}) {
    let response;
    try { response = await fetch(url,options); }
    catch(e) { throw new Error('Cannot reach the local server. Is run.py still running?'); }
    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const body=await response.json();
        if (Array.isArray(body.detail)) message=body.detail.map(x=>x.msg).join('; ');
        else message=String(body.detail||message);
      } catch {}
      throw new Error(message);
    }
    return response;
  }
  function setProject(p) {
    for (const value of state.pendingPatches.values()) clearTimeout(value.timer);
    state.pendingPatches.clear();
    state.patchGeneration++;
    state.project=p;
    state.selected=null;
    state.hovered=null;
    state.dropTarget=null;
    state.pointerDrag=null;
    state.imageCache.clear();
    try { localStorage.setItem('collage_studio_project_id',p.id); } catch {}
    shell.style.aspectRatio=`${p.ratio_width} / ${p.ratio_height}`;
    updateAll();
    saveStatus('All changes saved');
  }
  function updateAll() {
    const p=state.project;
    if (!p) return;
    const count=expectedCells(p), filled=Object.keys(p.tiles).length, remaining=count-filled;
    $('projectName').textContent=p.title||'Untitled collage';
    $('layoutSummary').textContent=`${p.columns} × ${p.rows} grid · ${p.ratio_width}:${p.ratio_height}`;
    $('settingGrid').textContent=`${p.columns} × ${p.rows}`;
    $('settingShowcase').textContent=`${p.showcase} × ${p.showcase} cells`;
    $('settingRatio').textContent=`${p.ratio_width} : ${p.ratio_height}`;
    $('ratioBadge').innerHTML=`${p.ratio_width}:${p.ratio_height} <span>${p.ratio_width<p.ratio_height?'PORTRAIT':p.ratio_width>p.ratio_height?'LANDSCAPE':'SQUARE'}</span>`;
    $('canvasDimensionNote').textContent=`${p.columns} × ${p.rows} photo grid`;
    $('filledCount').textContent=filled;
    $('emptyCount').textContent=remaining;
    $('totalCount').textContent=count;
    $('imageStatus').textContent=`${filled} / ${count} images placed`;
    $('percentageLabel').textContent=`${Math.round(filled/count*100)}% complete`;
    $('progressBar').style.width=`${filled/count*100}%`;
    $('canvasHelperText').textContent=state.fineTune
      ? 'Select a filled cell, then drag to reposition the image or adjust the sliders.'
      : 'Click any square to upload a photo, or drag and drop images onto the canvas.';
    $('fineTuneBtn').classList.toggle('active',state.fineTune);
    $('addModeBtn').classList.toggle('active',!state.fineTune);
    $('fineTuneBtn').setAttribute('aria-pressed',String(state.fineTune));
    $('addModeBtn').setAttribute('aria-pressed',String(!state.fineTune));
    updateInspector();
    draw();
    updateRenderEstimate();
  }
  function setFineTune(on) {
    state.fineTune=on;
    if (on && (!state.selected || !state.project.tiles[state.selected])) {
      state.selected=state.project.tiles.showcase ? 'showcase' : (Object.keys(state.project.tiles)[0] || null);
    }
    updateAll();
  }
  function updateInspector() {
    const key=state.selected;
    const tile = key && state.project && state.project.tiles[key];
    const active=!!(state.fineTune && tile);
    $('emptyInspector').hidden=active;
    $('activeInspector').hidden=!active;
    $('enableFineTuneBtn').textContent=state.fineTune ? 'Select a filled image on the canvas' : 'Enter Fine-tune mode';
    $('enableFineTuneBtn').disabled=state.fineTune;
    if (!active) return;
    $('selectionLabel').textContent=cellName(key);
    $('selectionFile').textContent=tile.original_filename;
    $('selectionThumbnail').style.backgroundImage=`url("${previewUrl(tile)}")`;
    $('zoomRange').value=Math.round(tile.zoom*100);
    $('panXRange').value=Math.round(tile.pan_x*100);
    $('panYRange').value=Math.round(tile.pan_y*100);
    $('zoomValue').textContent=`${Math.round(tile.zoom*100)}%`;
    $('panXValue').textContent=String(Math.round(tile.pan_x*100));
    $('panYValue').textContent=String(Math.round(tile.pan_y*100));
  }
  function selectCell(key) { state.selected=key; updateInspector();draw(); }
  function chooseImage(key) {
    if (!state.project || state.busy) return;
    state.targetUpload=key;
    $('photoFileInput').value='';
    $('photoFileInput').click();
  }
  async function uploadImage(key,file) {
    if (!key || !file || !state.project || state.busy) return;
    state.busy=true;
    saveStatus('Uploading photo…');
    const form=new FormData();form.append('cell',key);form.append('file',file,file.name);
    try {
      const response=await request(`/api/projects/${state.project.id}/images`,{method:'POST',body:form});
      state.project=await response.json();
      state.selected=key;
      saveStatus('All changes saved');
      updateAll();
      toast(`${cellName(key)} updated`);
    } catch(e) { saveStatus('Upload failed');toast(e.message,true); }
    finally { state.busy=false; }
  }
  function queuePatch(entry) {
    state.patchSequence = state.patchSequence.catch(() => {}).then(async () => {
      try {
        await request(`/api/projects/${entry.projectId}/tiles/${entry.cell}`, {
          method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(entry.transform)
        });
        if (state.patchGeneration === entry.generation && !state.pendingPatches.size) {
          saveStatus('All changes saved');
        }
      } catch (e) {
        if (state.patchGeneration === entry.generation) {
          saveStatus('Save failed');
          toast(e.message,true);
        }
      }
    });
    return state.patchSequence;
  }
  function flushPatch(token) {
    const entry=state.pendingPatches.get(token);
    if (!entry) return state.patchSequence;
    clearTimeout(entry.timer);
    state.pendingPatches.delete(token);
    return queuePatch(entry);
  }
  async function flushTransforms() {
    for (const token of Array.from(state.pendingPatches.keys())) flushPatch(token);
    await state.patchSequence;
  }
  function scheduleTransform() {
    const key=state.selected,p=state.project;
    if (!key || !p.tiles[key]) return;
    const token=`${p.id}/${key}`;
    const existing=state.pendingPatches.get(token);
    if (existing) clearTimeout(existing.timer);
    const entry={projectId:p.id,cell:key,generation:state.patchGeneration,
      transform:{zoom:p.tiles[key].zoom,pan_x:p.tiles[key].pan_x,pan_y:p.tiles[key].pan_y}};
    entry.timer=setTimeout(()=>flushPatch(token),280);
    state.pendingPatches.set(token,entry);
    saveStatus('Saving adjustments…');
  }
  function applyTransform(which,value) {
    const tile=state.project?.tiles[state.selected];if(!tile)return;
    if(which==='zoom') tile.zoom=clamp(value/100,1,4);
    if(which==='pan_x') tile.pan_x=clamp(value/100,-1,1);
    if(which==='pan_y') tile.pan_y=clamp(value/100,-1,1);
    updateInspector();draw();scheduleTransform();
  }
  async function deleteSelected() {
    const key=state.selected;
    if (!key || !state.project.tiles[key]) return;
    if (!confirm(`Remove the image from ${cellName(key)}?`))return;
    try {
      await flushTransforms();
      const response=await request(`/api/projects/${state.project.id}/tiles/${key}`,{method:'DELETE'});
      state.project=await response.json();state.selected=null;updateAll();toast('Image removed');
    } catch(e){toast(e.message,true);}
  }
  function pointEventKey(e) { return cellFromPoint(e.clientX,e.clientY); }
  canvas.addEventListener('pointerdown',(e)=>{
    if (e.button!==0 || !state.project)return;
    const key=pointEventKey(e);if (!key)return;
    if(state.fineTune){
      selectCell(key);
      const tile=state.project.tiles[key];
      if(!tile){chooseImage(key);return;}
      const img=getImage(tile),rect=rectFor(key,state.project,canvas.clientWidth,canvas.clientHeight);
      if(img.naturalWidth && rect){
        const m=imageMetrics(img,tile,rect);
        state.pointerDrag={key,startX:e.clientX,startY:e.clientY,panX:tile.pan_x,panY:tile.pan_y,overflowX:m.ox,overflowY:m.oy};
        canvas.setPointerCapture(e.pointerId);
        canvas.style.cursor='grabbing';
      }
    } else {chooseImage(key);}
  });
  canvas.addEventListener('pointermove',(e)=>{
    if(!state.project)return;
    if(state.pointerDrag){
      const d=state.pointerDrag,t=state.project.tiles[d.key];
      if(!t)return;
      const r=canvas.getBoundingClientRect();
      const sx=canvas.clientWidth/r.width, sy=canvas.clientHeight/r.height;
      if(d.overflowX>0.1)t.pan_x=clamp(d.panX-2*(e.clientX-d.startX)*sx/d.overflowX,-1,1);
      if(d.overflowY>0.1)t.pan_y=clamp(d.panY-2*(e.clientY-d.startY)*sy/d.overflowY,-1,1);
      updateInspector();draw();scheduleTransform();
    } else {
      const key=pointEventKey(e);
      if(key!==state.hovered){state.hovered=key;draw();}
      if(state.fineTune){canvas.style.cursor=key&&state.project.tiles[key]?'grab':'pointer';}
    }
  });
  function stopDragging(){state.pointerDrag=null;canvas.style.cursor=state.fineTune?'grab':'crosshair';}
  canvas.addEventListener('pointerup',stopDragging);
  canvas.addEventListener('pointercancel',stopDragging);
  canvas.addEventListener('pointerleave',()=>{if(!state.pointerDrag){state.hovered=null;draw();}});
  canvas.addEventListener('dblclick',(e)=>{const key=pointEventKey(e);if(state.fineTune&&key)chooseImage(key);});
  canvas.addEventListener('keydown',(e)=>{
    if(!state.project)return;
    if(e.key==='Enter' || e.key===' '){e.preventDefault();chooseImage(state.selected||'showcase');}
  });
  shell.addEventListener('dragenter',e=>{e.preventDefault();shell.classList.add('drag-over');});
  shell.addEventListener('dragover',e=>{
    e.preventDefault();e.dataTransfer.dropEffect='copy';
    state.dropTarget=cellFromPoint(e.clientX,e.clientY);draw();
  });
  shell.addEventListener('dragleave',e=>{
    if(!shell.contains(e.relatedTarget)){state.dropTarget=null;shell.classList.remove('drag-over');draw();}
  });
  shell.addEventListener('drop',e=>{
    e.preventDefault();shell.classList.remove('drag-over');
    const key=cellFromPoint(e.clientX,e.clientY),file=e.dataTransfer.files[0];
    state.dropTarget=null;draw();
    if(file && key)uploadImage(key,file);
  });
  $('photoFileInput').addEventListener('change',e=>{
    const file=e.target.files[0];if(file)uploadImage(state.targetUpload,file);
  });

  async function createProject(layout) {
    try {
      saveStatus('Creating project…');
      await flushTransforms();
      const response=await request('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(layout)});
      setProject(await response.json());
      $('newProjectDialog').close();
      toast('New collage created');
    } catch(e){toast(e.message,true);}
  }
  function readLayoutFields() {
    return {columns:+$('newColumns').value,rows:+$('newRows').value,showcase:+$('newShowcase').value,
      ratio_width:+$('newRatioW').value,ratio_height:+$('newRatioH').value};
  }
  function layoutNotice() {
    const p=readLayoutFields(),notice=$('layoutPreviewNotice');
    if(!Number.isFinite(p.columns)||!Number.isFinite(p.rows)||p.showcase>Math.min(p.columns,p.rows)||p.showcase<1){
      notice.textContent='Showcase must fit within the row and column counts.';
      notice.classList.add('warning');
    }else{
      notice.classList.remove('warning');
      notice.textContent=`${p.columns} × ${p.rows} grid with ${p.showcase} × ${p.showcase} showcase · ${expectedCells(p)} photos total`;
    }
  }
  ['newColumns','newRows','newShowcase','newRatioW','newRatioH'].forEach(id=>$(id).addEventListener('input',layoutNotice));
  function openNewProject() {
    if(!state.project)return;
    const p=state.project;
    $('newColumns').value=p.columns;
    $('newRows').value=p.rows;
    $('newShowcase').value=p.showcase;
    $('newRatioW').value=p.ratio_width;
    $('newRatioH').value=p.ratio_height;
    layoutNotice();$('newProjectDialog').showModal();
  }
  $('newProjectBtn').addEventListener('click',openNewProject);
  $('changeLayoutBtn').addEventListener('click',openNewProject);
  $('newProjectForm').addEventListener('submit',e=>{
    e.preventDefault();const p=readLayoutFields();
    if(p.showcase>Math.min(p.columns,p.rows)){toast('Showcase must fit within grid',true);return;}
    if(state.project && Object.keys(state.project.tiles).length){
      if(!confirm('Creating a new project switches away from the current one. Save a project ZIP first if you want to reopen it later. Continue?'))return;
    }
    createProject(p);
  });
  document.querySelectorAll('[data-close]').forEach(el=>el.addEventListener('click',()=>$(el.dataset.close).close()));
  $('addModeBtn').addEventListener('click',()=>setFineTune(false));
  $('fineTuneBtn').addEventListener('click',()=>setFineTune(true));
  $('enableFineTuneBtn').addEventListener('click',()=>setFineTune(true));
  $('gridLinesCheck').addEventListener('change',e=>{state.showGrid=e.target.checked;draw();});
  $('zoomRange').addEventListener('input',e=>applyTransform('zoom',+e.target.value));
  $('panXRange').addEventListener('input',e=>applyTransform('pan_x',+e.target.value));
  $('panYRange').addEventListener('input',e=>applyTransform('pan_y',+e.target.value));
  $('resetCropBtn').addEventListener('click',()=>{
    const tile=state.project?.tiles[state.selected];if(!tile)return;
    tile.zoom=1;tile.pan_x=0;tile.pan_y=0;
    updateInspector();draw();scheduleTransform();toast('Crop reset');
  });
  $('replacePhotoBtn').addEventListener('click',()=>chooseImage(state.selected));
  $('removePhotoBtn').addEventListener('click',deleteSelected);

  function anchorDownload(url) {
    const a=document.createElement('a');a.href=url;a.download='';document.body.appendChild(a);a.click();a.remove();
  }
  $('exportZipBtn').addEventListener('click',async()=>{if(state.project){await flushTransforms();anchorDownload(`/api/projects/${state.project.id}/bundle.zip`);toast('Project ZIP download started');}});
  $('exportJsonBtn').addEventListener('click',async()=>{if(state.project){await flushTransforms();anchorDownload(`/api/projects/${state.project.id}/config.json`);toast('Config JSON download started');}});
  $('openZipBtn').addEventListener('click',()=>{ $('zipFileInput').value='';$('zipFileInput').click(); });
  $('zipFileInput').addEventListener('change',async e=>{
    const file=e.target.files[0];if(!file)return;
    const form=new FormData();form.append('file',file,file.name);
    saveStatus('Opening project…');
    try {
      await flushTransforms();
      const response=await request('/api/import-zip',{method:'POST',body:form});
      setProject(await response.json());toast('Editable project loaded');
    }catch(err){saveStatus('Import failed');toast(err.message,true);}
  });

  function renderParameters(){
    return {width:+$('outputWidth').value,unit:$('outputUnit').value,
      dpi:+$('outputDpi').value,quality:+$('outputQuality').value};
  }
  function pixelDimensions(p,parameters) {
    const unitFactor={px:1,cm:parameters.dpi/2.54,mm:parameters.dpi/25.4,in:parameters.dpi}[parameters.unit];
    const pw=Math.round(parameters.width*unitFactor);
    const ph=Math.round(pw*p.ratio_height/p.ratio_width);
    return {pw,ph};
  }
  function updateRenderEstimate() {
    if(!state.project)return;
    const p=state.project, params=renderParameters(),{pw,ph}=pixelDimensions(p,params);
    const h=params.width*p.ratio_height/p.ratio_width;
    $('outputHeight').value=Number.isFinite(h)? `${+h.toFixed(3)} ${params.unit}`:'—';
    $('pixelEstimate').textContent=Number.isFinite(pw)&&Number.isFinite(ph)?`${niceNumber(pw)} × ${niceNumber(ph)} px`:'Invalid dimensions';
    const warning=$('renderWarning');
    const invalid=!Number.isFinite(pw)||!Number.isFinite(ph)||pw<1||ph<1||pw>30000||ph>30000||pw*ph>LIMIT_PIXELS||params.dpi<36||params.dpi>1200||params.width<=0;
    const remaining=expectedCells(p)-Object.keys(p.tiles).length;
    warning.hidden=!invalid && remaining===0;
    if(invalid)warning.textContent='Output is invalid or exceeds the maximum of 80 megapixels and 30,000 px per side. Choose a smaller size.';
    else if(remaining)warning.textContent=`${remaining} ${remaining===1?'cell is':'cells are'} still empty. Empty spaces will render in light gray.`;
    $('confirmRenderBtn').disabled=invalid;
  }
  ['outputWidth','outputUnit','outputDpi','outputQuality'].forEach(id=>$(id).addEventListener('input',updateRenderEstimate));
  $('outputUnit').addEventListener('change',e=>{
    $('outputWidth').value={cm:20,mm:200,in:8,px:2400}[e.target.value];
    updateRenderEstimate();
  });
  $('renderBtn').addEventListener('click',()=>{$('renderDialog').showModal();updateRenderEstimate();});
  $('renderForm').addEventListener('submit',async e=>{
    e.preventDefault();if(!state.project||state.busy)return;
    const params=renderParameters();const {pw,ph}=pixelDimensions(state.project,params);
    if(pw*ph>LIMIT_PIXELS || pw<1 || ph<1)return;
    const button=$('confirmRenderBtn');button.disabled=true;button.textContent='Rendering…';
    state.busy=true;saveStatus('Rendering JPEG…');
    try {
      await flushTransforms();
      const response=await request(`/api/projects/${state.project.id}/render`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)
      });
      const blob=await response.blob(),url=URL.createObjectURL(blob);
      const a=document.createElement('a');a.href=url;a.download=`collage-${pw}x${ph}-${params.dpi}dpi.jpg`;
      document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
      $('renderDialog').close();toast(`JPEG rendered — ${niceNumber(pw)} × ${niceNumber(ph)} pixels`);
      saveStatus('All changes saved');
    }catch(err){toast(err.message,true);saveStatus('Render failed');}
    finally{state.busy=false;button.disabled=false;button.innerHTML='Download JPEG <span class="button-arrow">→</span>';}
  });

  new ResizeObserver(draw).observe(shell);
  window.addEventListener('resize',draw);
  window.addEventListener('pagehide',()=>{
    // A local asynchronous save has already been queued for active slider changes.
    for (const token of Array.from(state.pendingPatches.keys())) flushPatch(token);
  });
  async function boot() {
    let id=null;
    try { id=localStorage.getItem('collage_studio_project_id'); } catch {}
    if(id){try {const response=await request(`/api/projects/${id}`);setProject(await response.json());return;}catch{}}
    await createProject({columns:10,rows:10,showcase:6,ratio_width:4,ratio_height:5});
  }
  boot();
})();
