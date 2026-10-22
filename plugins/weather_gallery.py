"""LRPT gallery and selected-pass image deletion for the main web console."""
from datetime import datetime, timezone
from functools import lru_cache
import html
from io import BytesIO
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlencode

EXTENSIONS = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
DELETE_LOCK = threading.Lock()


def delete_pass_images(root, selected):
    """Delete selected-pass images, local IQ recordings and logs; retain job history."""
    if (not isinstance(selected, list) or not selected or
            not all(isinstance(name, str) and name not in ('', '.', '..') and
                    Path(name).name == name and ':' not in name for name in selected) or
            len(set(selected)) != len(selected)):
        raise ValueError('Select one or more different passes')
    base = archive_root(root).resolve()
    with DELETE_LOCK:
        plans = []
        # Validate every pass before deleting anything.
        for name in selected:
            folder = base/name
            if not folder.is_dir() or folder.resolve() != folder.absolute():
                raise ValueError('Selected pass is unavailable: ' + name)
            job_path = folder/'job.json'
            if job_path.is_symlink() or job_path.resolve() != job_path.absolute():
                raise ValueError('Linked job metadata is not supported')
            job = json.loads(job_path.read_text(encoding='utf-8')) if job_path.exists() else None
            if job is not None and not isinstance(job, dict):
                raise ValueError('Invalid pass metadata: ' + name)
            if job and job.get('state') in ('capturing', 'captured', 'queued', 'decoding'):
                raise ValueError('Wait for capture and decoding to finish: ' + name)
            products = folder/'products'
            images = []
            if products.resolve() != products.absolute():
                raise ValueError('Linked product directories are not supported')
            def walk_error(error):
                raise error
            for directory, dirs, files in os.walk(products, followlinks=False, onerror=walk_error):
                for child in dirs:
                    path = Path(directory)/child
                    if path.is_symlink() or path.resolve() != path.absolute():
                        raise ValueError('Linked product directories are not supported')
                for filename in files:
                    path = Path(directory)/filename
                    if path.suffix.lower() in EXTENSIONS:
                        if path.is_symlink() or resolve_image(root, path.relative_to(base).as_posix()) is None:
                            raise ValueError('Unsafe image path: ' + filename)
                        images.append(path)
            # Never follow a replay's iq_file reference into another pass or an
            # external archive: it can be shared by several decoding jobs.
            for path in folder.iterdir():
                if path.suffix.lower() in ('.sigmf-data', '.sigmf-meta', '.log'):
                    if path.is_symlink() or path.resolve() != path.absolute() or not path.is_file():
                        raise ValueError('Unsafe recording/log path: ' + path.name)
                    images.append(path)
            plans.append((name, folder, job, images))
        deleted, completed, errors = [], [], []
        for name, folder, job, images in plans:
            pass_errors = []
            removed = set()
            for image in images:
                try:
                    image.unlink()
                    removed.add(image.relative_to(folder).as_posix())
                    deleted.append(image.relative_to(base).as_posix())
                except OSError as exc:
                    pass_errors.append(dict(pass_id=name, message=f'{image.name}: {exc}'))
            if job is not None:
                job['images'] = [item for item in job.get('images', []) if Path(item).as_posix() not in removed]
                orientation = job.get('orientation')
                if isinstance(orientation, dict):
                    orientation['replacements'] = {key: value for key, value in orientation.get('replacements', {}).items()
                                                   if key not in removed and value not in removed}
                job['files_deleted_at'] = datetime.now(timezone.utc).isoformat()
                job['deleted_files'] = sorted(set(job.get('deleted_files', [])) | removed)
                try:
                    from plugins.bqe_lrpt_decoder.engine import write_json
                    write_json(folder/'job.json', job)
                except OSError as exc:
                    pass_errors.append(dict(pass_id=name, message='Could not update job details: ' + str(exc)))
            if not pass_errors:
                completed.append(name)
            errors.extend(pass_errors)
        _thumbnail.cache_clear()
        return dict(deleted=deleted, completed=completed, errors=errors,
                    message=f'Deleted {len(deleted)} image, recording and log file(s) from {len(selected)} selected pass(es).')


def archive_root(root):
    return Path(root) / 'plugins' / 'bqe_lrpt_decoder' / 'recordings'


def resolve_image(root, name):
    base = archive_root(root).resolve()
    relative = Path(name)
    if relative.is_absolute() or len(relative.parts) < 3 or relative.parts[1] != 'products':
        return None
    candidate = base / relative
    try:
        if any(part in ('.', '..') for part in relative.parts):
            return None
        # Reject links/junctions, including those pointing elsewhere in the archive.
        if candidate.resolve() != candidate.absolute():
            return None
        candidate.resolve().relative_to(base)
        if candidate.is_file() and candidate.suffix.lower() in EXTENSIONS:
            return candidate
    except (OSError, ValueError):
        pass
    return None


def passes(root):
    base = archive_root(root)
    result = []
    if not base.exists():
        return result
    for folder in base.iterdir():
        if not folder.is_dir() or folder.resolve() != folder.absolute():
            continue
        try:
            job = json.loads((folder / 'job.json').read_text(encoding='utf-8'))
            if not isinstance(job, dict):
                job = {}
        except (OSError, ValueError):
            job = {}
        stamp = job.get('captured_at') or job.get('started')
        try:
            when = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
            when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
        except ValueError:
            try:
                when = datetime.strptime(folder.name[:16], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
            except ValueError:
                when = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc)
        images = []
        replacements = (job.get('orientation') or {}).get('replacements', {})
        for image in sorted((folder / 'products').rglob('*')):
            name = image.relative_to(base).as_posix()
            replacement = replacements.get(image.relative_to(folder).as_posix())
            if replacement and resolve_image(root, folder.name + '/' + replacement):
                continue
            if resolve_image(root, name) and image.stat().st_size:
                images.append(name)
        if images:
            options = job.get('options') or {}
            satellite = options.get('satellite', 'Meteor') if isinstance(options, dict) else 'Meteor'
            result.append(dict(id=folder.name, when=when.astimezone(timezone.utc), satellite=satellite, images=images))
    return sorted(result, key=lambda item: (item['when'], item['id']), reverse=True)


@lru_cache(maxsize=128)
def _thumbnail(path, mtime, size):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        source.thumbnail((360, 240))
        picture = ImageOps.exif_transpose(source).convert('RGB')
        output = BytesIO()
        picture.save(output, 'JPEG', quality=82)
        return output.getvalue()


def thumbnail(path):
    stat = path.stat()
    return _thumbnail(str(path), stat.st_mtime_ns, stat.st_size)


def url(route, name):
    return route + '?' + urlencode({'name': name})


def gallery_html(root, theme='modern'):
    groups = passes(root)
    sections = []
    for group in groups:
        cards = []
        for name in group['images']:
            label = '/'.join(Path(name).parts[2:])
            cards.append('<a class="card" target="_blank" rel="noopener" href="' +
                         html.escape(url('/weather-viewer', name), quote=True) + '"><img loading="lazy" src="' +
                         html.escape(url('/weather-thumbnail', name), quote=True) + '" alt="' + html.escape(label, quote=True) +
                         '"><span>' + html.escape(label) + '</span></a>')
        sections.append('<section><h2><label><input type="checkbox" class="pass-select" data-pass="' +
                        html.escape(group['id'], quote=True) + '"> ' + group['when'].strftime('%Y-%m-%d · %H:%M:%S UTC') +
                        ' — ' + html.escape(str(group['satellite'])) + '</label></h2><p class="muted">Pass ' +
                        html.escape(group['id']) + '</p><div class="grid">' + ''.join(cards) + '</div></section>')
    content = ''.join(sections) or '<p class="empty">No LRPT images received yet. Decoded images will appear here after a pass.</p>'
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BQE WISP Weather</title><style>
*{box-sizing:border-box}body{margin:0;background:#edf3fa;color:#17314e;font:16px system-ui,sans-serif}
header{background:#173c65;color:white;padding:22px 28px;display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}
button:disabled{opacity:.5;cursor:default}.pass-select{width:18px;height:18px}#deleteStatus{padding:12px 28px}
h1{margin:0}header p{margin:8px 0 0}button{padding:9px 18px;cursor:pointer}main{padding:8px 28px 32px}
section{margin:24px 0 36px}h2{font-size:20px;margin-bottom:4px}.muted{color:#51667d;font-size:13px;margin-top:0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px}
.card{display:flex;flex-direction:column;background:white;border:1px solid #c5d4e4;border-radius:8px;overflow:hidden;color:inherit;text-decoration:none}
.card:hover,.card:focus-visible{outline:3px solid #3987c7}.card img{width:100%;height:220px;object-fit:contain;background:#14202d}
.card span{padding:12px;overflow-wrap:anywhere}.empty{padding:35px 0}
body.classic{background:#d4d0c8;color:black;font-family:Arial,sans-serif}body.classic header{background:#000080}
body.classic .card{border-radius:0;border:2px outset #eee}body.classic .muted{color:#444}
</style><body class="''' + ('classic' if theme == 'classic' else 'modern') + '''"><header><div><h1>Weather</h1>
<p>LRPT images by pass · Newest first · Times in UTC</p></div><div>
<button id="deletePassImages" disabled>Delete selected passes’ images, recordings and logs</button>
<button onclick="location.reload()">Refresh</button></div></header>
<div id="deleteStatus" role="status" aria-live="polite">Select passes to delete their images, local IQ recordings and decoding logs.</div>
<main>''' + content + '''</main><script>
document.querySelectorAll('.card').forEach(card=>card.addEventListener('click',event=>{
 if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
 const viewer=window.open(card.href,'_blank','popup=yes,width=1200,height=850,resizable=yes,scrollbars=yes');
 if(viewer){viewer.opener=null;event.preventDefault();}
}));
const deleteButton=document.getElementById('deletePassImages');
const deleteStatus=document.getElementById('deleteStatus');
const passBoxes=()=>[...document.querySelectorAll('.pass-select')];
let deleting=false;
function updateSelection(){
 const count=passBoxes().filter(box=>box.checked).length;
 deleteButton.disabled=deleting||count===0;
 if(!deleting)deleteStatus.textContent=`${count} pass(es) selected. Images, local IQ recordings and decoding logs will be deleted.`;
}
passBoxes().forEach(box=>box.addEventListener('change',updateSelection));
deleteButton.addEventListener('click',async()=>{
 const passes=passBoxes().filter(box=>box.checked).map(box=>box.dataset.pass);
 if(deleting||!passes.length)return;
 deleting=true;updateSelection();passBoxes().forEach(box=>box.disabled=true);
 deleteStatus.textContent='Deleting images, recordings and logs from selected passes…';
 try{
  const response=await fetch('/api/weather/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passes})});
  const result=await response.json();
  if(!response.ok)throw new Error(result.message||'Deletion failed');
  passBoxes().forEach(box=>{if(result.completed.includes(box.dataset.pass))box.closest('section').remove();});
  deleteStatus.textContent=result.message+(result.errors||[]).map(error=>` ${error.pass_id}: ${error.message}`).join('');
  if(result.errors?.length)deleteStatus.textContent+=' Refresh to see remaining images.';
 }catch(error){deleteStatus.textContent=error.message;}
 finally{deleting=false;passBoxes().forEach(box=>box.disabled=false);deleteButton.disabled=!passBoxes().some(box=>box.checked);}
});
</script></body></html>'''


def viewer_html(name):
    label = html.escape(Path(name).name)
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><title>' + label +
            '</title><style>body{margin:0;background:#111;color:white;font:14px system-ui}header{padding:12px;position:sticky;left:0}'
            'img{display:block;max-width:none;max-height:none}a{color:#aaddff}</style><header>' + label +
            ' · Full resolution (100%) · Scroll to explore · <a target="_blank" rel="noopener" href="' +
            html.escape(url('/weather-image', name), quote=True) + '">Original image</a></header><img alt="' +
            label + '" src="' + html.escape(url('/weather-image', name), quote=True) + '"></html>')
