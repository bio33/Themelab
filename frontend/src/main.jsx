import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const API = 'http://127.0.0.1:8000';

async function api(url, options = {}) {
  let response;
  try { response = await fetch(`${API}${url}`, options); }
  catch { throw new Error('The local FastAPI server is not running. Start the backend and refresh.'); }
  let data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function Icon({name, size=20}) {
  const p = { width:size, height:size, viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:1.7, strokeLinecap:'round', strokeLinejoin:'round' };
  const shapes = {
    phone:<><rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18.5h2"/></>,
    refresh:<><path d="M3 12a9 9 0 0 1 15.4-6.4L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15.4 6.4L3 16"/><path d="M3 21v-5h5"/></>,
    image:<><rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></>,
    plus:<path d="M12 5v14M5 12h14"/>,
    upload:<><path d="M12 16V3m-5 5 5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/></>,
    arrow:<path d="m5 12 14 0m-6-6 6 6-6 6"/>,
    check:<path d="m5 12 4 4L19 6"/>,
    monitor:<><rect x="2" y="3" width="20" height="15" rx="2"/><path d="M8 22h8M12 18v4"/></>,
    info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v5m0-9v.2"/></>,
    spark:<><path d="m12 2 1.8 6.2L20 10l-6.2 1.8L12 18l-1.8-6.2L4 10l6.2-1.8L12 2ZM19 16l.9 2.1L22 19l-2.1.9L19 22l-.9-2.1L16 19l2.1-.9L19 16Z"/></>,
    bolt:<path d="m13 2-8 11h7l-1 9 8-12h-7l1-8Z"/>,
    unlock:<><rect x="5" y="11" width="14" height="11" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0"/></>,
  };
  return <svg {...p} aria-hidden="true">{shapes[name] || shapes.info}</svg>;
}

function App() {
  const [health, setHealth] = useState(null);
  const [devices, setDevices] = useState([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState(null);
  const [themes, setThemes] = useState([]);
  const [themeId, setThemeId] = useState('');
  const [themeName, setThemeName] = useState('');
  const [screenshot, setScreenshot] = useState('');
  const [busy, setBusy] = useState('');
  const [notice, setNotice] = useState(null);
  const active = useMemo(() => themes.find(t => t.id === themeId), [themes, themeId]);
  const device = devices.find(d => d.serial === selected);
  const ready = device && device.status === 'device';
  
  async function refreshAll() {
    try {
      const [h, ts] = await Promise.all([api('/api/health'), api('/api/themes')]);
      setHealth(h); setThemes(ts.themes);
      setThemeId(v => ts.themes.some(t=>t.id===v) ? v : (ts.themes[0]?.id || ''));
      if (h.adb) {
        const ds = await api('/api/devices');
        setDevices(ds.devices);
        setSelected(v => ds.devices.some(d => d.serial === v) ? v : (ds.devices.find(d=>d.status==='device')?.serial || ds.devices[0]?.serial || ''));
      } else {
        setDevices([]); setSelected('');
      }
    } catch (e) { setNotice({type:'error', text:e.message}); }
  }
  useEffect(() => { refreshAll(); }, []);
  useEffect(() => {
    setScreenshot(''); setDetail(null);
    if (ready) api(`/api/devices/${encodeURIComponent(selected)}`).then(setDetail).catch(e => setNotice({type:'error',text:e.message}));
  }, [selected, device?.status]);
  
  async function task(key, fn) {
    setBusy(key); setNotice(null);
    try { let result = await fn(); if (result) setNotice({type:'success',text:result.note || result.message || 'Completed.'}); }
    catch (e) { setNotice({type:'error',text:e.message}); }
    finally { setBusy(''); }
  }
  async function createTheme(e) {
    e.preventDefault();
    if (!themeName.trim()) return;
    await task('create', async () => {
      const created = await api('/api/themes', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:themeName.trim()})});
      setThemes(v => [created,...v]); setThemeId(created.id); setThemeName('');
      return {note:`Created theme: ${created.name}`};
    });
  }
  async function uploadWallpaper(e) {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file || !themeId) return;
    const fd = new FormData(); fd.append('file',file);
    await task('upload', async () => {
      const updated = await api(`/api/themes/${themeId}/wallpaper`, {method:'POST',body:fd});
      setThemes(v=>v.map(t=>t.id===updated.id?updated:t));
      return {note:`Imported ${updated.wallpaper.width} × ${updated.wallpaper.height} wallpaper.`};
    });
  }
  async function screenshotNow() {
    await task('screenshot', async () => {
      const res = await fetch(`${API}/api/devices/${encodeURIComponent(selected)}/screenshot`);
      if (!res.ok) throw new Error((await res.json()).detail || 'Screenshot failed');
      const obj = URL.createObjectURL(await res.blob());
      setScreenshot(old => { if (old.startsWith('blob:')) URL.revokeObjectURL(old); return obj; });
      return {note:'Screen captured locally. This image is not uploaded to a cloud service.'};
    });
  }

  return <div className="app">
    <aside className="sidebar">
      <div className="brand"><div className="logo"><Icon name="spark" size={23}/></div><div><strong>ThemeLab</strong><small>Android Customization Studio</small></div></div>
      <div className="side-section">WORKSPACE</div>
      <div className="navitem active"><Icon name="monitor"/> Dashboard</div>
      <div className="navitem"><Icon name="phone"/> Device manager <span className="count">{devices.length}</span></div>
      <div className="navitem"><Icon name="image"/> Theme library <span className="count">{themes.length}</span></div>
      <div className="side-bottom"><div className="statusline"><span className={`statusdot ${health?.adb?'green':'red'}`}/>{health?.adb?'ADB ready':'ADB unavailable'}</div><p>Local-first. No account required. All ADB actions stay on your computer.</p><span className="version">MVP · v0.1.0</span></div>
    </aside>
    <main>
      <header><div><div className="eyebrow">DEVICE CUSTOMIZATION</div><h1>Control center</h1><p>Connect phones, organize themes, and transfer custom wallpapers.</p></div><button className="button outlined" onClick={refreshAll} disabled={!!busy}><Icon name="refresh" size={17}/> Refresh devices</button></header>
      {notice && <div className={`alert ${notice.type}`}><Icon name={notice.type==='error'?'info':'check'} size={18}/><span>{notice.text}</span><button onClick={()=>setNotice(null)} aria-label="Dismiss">×</button></div>}
      {!health?.adb && <div className="alert error"><Icon name="info" size={18}/>ADB could not be found. Add Android SDK Platform-Tools to PATH, then restart the backend.</div>}
      <div className="main-grid">
        <section className="panel"><div className="panel-top"><div><h2>Connected devices</h2><p>Select a phone to target all actions</p></div><span className="pill">{devices.length} detected</span></div>
          <div className="device-list">{devices.length===0 ? <div className="empty"><Icon name="phone" size={34}/><strong>No devices detected</strong><p>Connect USB, enable USB debugging, and approve this computer on your phone.</p></div> : devices.map(d=><button key={d.serial} onClick={()=>setSelected(d.serial)} className={`device-option ${selected===d.serial?'chosen':''}`}><span className="device-icon"><Icon name="phone" size={24}/></span><span className="device-copy"><strong>{d.model_hint?.replaceAll('_',' ') || d.serial}</strong><small>{d.serial}</small></span><span className={`tiny-status ${d.status==='device'?'ok':'warn'}`}>{d.status==='device'?'Ready':d.status}</span></button>)}</div>
          {detail && <div className="properties"><div><small>Manufacturer</small><strong>{detail.manufacturer || 'Unknown'}</strong></div><div><small>Android</small><strong>{detail.android || '—'}</strong></div><div><small>Display</small><strong>{detail.resolution || '—'}</strong></div><div><small>Battery</small><strong>{detail.battery_percent == null ? '—' : `${detail.battery_percent}%`}</strong></div></div>}
          {device && !ready && <div className="warning">Unlock the phone and accept its USB debugging prompt. Then refresh devices.</div>}
          <div className="panel-actions"><button className="button outlined small" onClick={screenshotNow} disabled={!ready || !!busy}><Icon name="image" size={16}/> {busy==='screenshot'?'Capturing...':'Capture screen'}</button><span>Read-only screenshot</span></div>
        </section>
        <section className="panel"><div className="panel-top"><div><h2>Device preview</h2><p>{detail?.model || 'Select an authorized Android device'}</p></div><span className="pill muted">{detail?.capabilities?.profile?.replaceAll('_',' ') || 'No profile'}</span></div>
          <div className="preview-area"><div className="phone-mock"><div className="mock-hole"/>{screenshot?<img src={screenshot} alt="Latest device screenshot"/>:<div className="mock-placeholder"><Icon name="phone" size={55}/><span>Live preview available<br/>after screen capture</span></div>}</div></div>
          <div className="preview-footer"><Icon name="info" size={17}/> Screenshot captures happen on demand, not continuously.</div>
        </section>
      </div>
      <section className="panel theme-panel"><div className="panel-top"><div><h2>Theme library</h2><p>Prepare art once and deploy it to any connected device.</p></div></div>
        <div className="theme-grid"><div className="theme-controls"><form onSubmit={createTheme} className="create-row"><input value={themeName} onChange={e=>setThemeName(e.target.value)} maxLength={64} placeholder="Theme name (e.g. Neon City)" aria-label="Theme name"/><button type="submit" disabled={!themeName.trim()||!!busy} className="button primary small"><Icon name="plus" size={17}/> Create</button></form>
          <label className="label">SELECT THEME</label><div className="theme-list">{themes.length===0?<div className="empty small-empty">Create your first theme to begin.</div>:themes.map(t=><button key={t.id} className={`theme-option ${themeId===t.id?'chosen':''}`} onClick={()=>setThemeId(t.id)}><span className="theme-thumbnail">{t.wallpaper?<img src={`${API}/api/themes/${t.id}/wallpaper`}/>:<Icon name="image"/>}</span><span><strong>{t.name}</strong><small>{t.wallpaper?`${t.wallpaper.width} × ${t.wallpaper.height}`:'No wallpaper yet'}</small></span>{themeId===t.id && <Icon name="check" size={18}/>}</button>)}</div>
          <label className={`button outlined upload-button ${!themeId||!!busy?'disabled':''}`}><Icon name="upload" size={17}/> Import wallpaper <input type="file" accept="image/png,image/jpeg,image/webp,image/bmp" onChange={uploadWallpaper} disabled={!themeId||!!busy}/></label>
        </div>
          <div className="theme-preview">{active?.wallpaper?<img src={`${API}/api/themes/${active.id}/wallpaper`} alt={`Wallpaper for ${active.name}`}/>:<div className="theme-placeholder"><Icon name="spark" size={35}/><strong>No wallpaper selected</strong><small>Use ChatGPT to create a wallpaper, then import the image.</small></div>}</div>
          <div className="deploy-area"><div className="label">DEPLOY TO DEVICE</div><h3>{device?.model_hint?.replaceAll('_',' ') || 'No phone selected'}</h3><p>ADB transfers the wallpaper into the phone's Pictures/ThemeLab folder. The wallpaper picker is opened separately.</p><div className="deploy-step"><span>1</span><div><strong>Stage wallpaper</strong><small>Copy PNG to selected device</small></div></div><button className="button primary full" disabled={!ready||!active?.wallpaper||!!busy} onClick={()=>task('stage',()=>api(`/api/devices/${encodeURIComponent(selected)}/stage-wallpaper/${themeId}`,{method:'POST'}))}><Icon name="upload" size={17}/>{busy==='stage'?'Transferring...':'Send wallpaper to phone'}</button><div className="deploy-step"><span>2</span><div><strong>Open wallpaper picker</strong><small>Choose image and confirm on phone</small></div></div><button className="button outlined full" disabled={!ready||!!busy} onClick={()=>task('picker',()=>api(`/api/devices/${encodeURIComponent(selected)}/open-wallpaper-picker`,{method:'POST'}))}>Open picker on phone <Icon name="arrow" size={17}/></button><p className="fine">One-click application requires a future Android companion. This MVP does not silently modify your wallpaper.</p></div>
        </div>
      </section>
      <div className="footnote"><Icon name="unlock" size={17}/> Security: connect only trusted devices; disable USB debugging when not in use. All operations target your selected ADB serial.</div>
    </main>
  </div>;
}

createRoot(document.getElementById('root')).render(<App/>);
