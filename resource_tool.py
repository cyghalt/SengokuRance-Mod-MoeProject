"""Read/extract resources; build patches only in this workspace, never in the game folder."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, base64, collections, hashlib, io, json, re, shutil, struct, subprocess, time
from PIL import Image

from runtime_paths import app_root
BASE = app_root()
GAME = Path('D:/Program Files (x86)/Steam/steamapps/common/Sengoku Rance')
ALICE = BASE / 'tools/alice.exe'
GROUPS = {'steam_current': 'rance7.ain'}
KEY = bytes.fromhex('5d91ae874a5641cd83ec4c92b5cb1634')

def digest(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(4*1024*1024),b''): h.update(b)
    return h.hexdigest()

def save_json(p, obj):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')

def call(args):
    r=subprocess.run([str(ALICE),*map(str,args)],capture_output=True)
    if r.returncode: raise RuntimeError(r.stderr.decode('utf8','replace'))
    return r.stdout

def u24(b): return int.from_bytes(b,'little')
def read32(b,o): return struct.unpack_from('<I',b,o)[0]
def rel(p): return str(Path(p).relative_to(BASE)).replace('\\','/')

def write_gallery(manifest):
    fields=('group','archive','pointer_index','alias_pointer_indices','resource_ids','resource_ids_zero_based','internal_name','png','format','width','height','alpha')
    gallery=[{k:im.get(k,[]) for k in fields} for im in manifest['images']]
    (BASE/'gallery-data.js').write_text('const RESOURCE_IMAGES = '+json.dumps(gallery,ensure_ascii=False,separators=(',',':'))+';\n','utf8')

def cgmetrics(b):
    if b[:4]==b'QNT\0':
        version=read32(b,4); shift=4 if version else 0
        x,y,w,h,bpp=struct.unpack_from('<5I',b,8+shift)
        return dict(format='qnt',width=w,height=h,x=x,y=y,alpha=bool(read32(b,36+shift)),version=version)
    if b[:4]==b'AJP\0':
        return dict(format='ajp',width=read32(b,12),height=read32(b,16),x=0,y=0,alpha=bool(read32(b,32)))
    raise ValueError('Unexpected CG signature')

def ald_extract(p, group):
    name=p.stem
    rd=BASE/'originals/graphics'/group/name; rd.mkdir(parents=True,exist_ok=True)
    ed=BASE/'editable/graphics'/group/name; ed.mkdir(parents=True,exist_ok=True)
    sd=BASE/'originals/archive_structure'/group/name; sd.mkdir(parents=True,exist_ok=True)
    images=[]; entries=[]
    with p.open('rb') as f:
        h=f.read(6); ptr_end=u24(h[:3])*256; map_end=u24(h[3:6])*256
        assert 0 < ptr_end <= map_end < p.stat().st_size
        f.seek(0); prefix=f.read(map_end); pointers=prefix[:ptr_end]; links=prefix[ptr_end:]
        volume=ord(name[-1].upper())-64
        ids=collections.defaultdict(list)
        for i in range(len(links)//3):
            d=links[i*3:i*3+3]
            if d[0]==volume and int.from_bytes(d[1:],'little'):
                ids[int.from_bytes(d[1:],'little')].append(i+1)
        addresses=[u24(pointers[i:i+3])*256 for i in range(0,len(pointers)-2,3)]
        count=0
        for pi,address in enumerate(addresses[1:],1):
            if address==p.stat().st_size-16:
                count=pi-1; break
        assert count>0 and addresses[1]==map_end
        for pi in range(1,count+1):
            start=addresses[pi]; end=addresses[pi+1]
            if start==end:
                entries.append(dict(pointer_index=pi,alias_to=pi+1))
                continue
            f.seek(start); fixed=f.read(16); hs,size=struct.unpack_from('<II',fixed)
            assert 16<hs<=4096 and start+hs+size<=end
            hdr=fixed+f.read(hs-16)
            filename=hdr[16:].split(b'\0',1)[0].decode('cp932')
            assert '/' not in filename and '\\' not in filename
            data=f.read(size); padding=f.read(end-start-hs-size)
            filename=f'{pi:05d}__{filename}'
            raw=rd/filename; raw.write_bytes(data)
            png=ed/(Path(filename).stem+'.png')
            aliases=[e['pointer_index'] for e in entries if e.get('alias_to')==pi]
            allids=ids[pi]+[i for a in aliases for i in ids[a]]
            im=dict(group=group,archive=p.name,pointer_index=pi,alias_pointer_indices=aliases,resource_ids=allids,
                    resource_ids_zero_based=[n-1 for n in allids],internal_name=hdr[16:].split(b'\0',1)[0].decode('cp932'),
                    raw=rel(raw),png=rel(png),raw_sha256=hashlib.sha256(data).hexdigest(),**cgmetrics(data))
            images.append(im)
            entries.append(dict(pointer_index=pi,header=base64.b64encode(hdr).decode(),
                                padding=base64.b64encode(padding).decode(),raw=rel(raw),raw_sha256=im['raw_sha256']))
        f.seek(addresses[count+1]);footer=f.read()
        assert footer[:4]==b'NL\x01\0' and len(footer)==16
    (sd/'prefix.bin').write_bytes(prefix);(sd/'footer.bin').write_bytes(footer)
    meta=dict(group=group,source=p.name,source_sha256=digest(p),source_bytes=p.stat().st_size,
              prefix=rel(sd/'prefix.bin'),footer=rel(sd/'footer.bin'),entries=entries)
    save_json(sd/'archive.json',meta)
    return images

def ain_sections(p):
    mp=call(['ain','dump','--map',p]).decode('utf8')
    tmp=BASE/'build/.decrypted.bin';tmp.parent.mkdir(exist_ok=True)
    call(['ain','dump','-d','-o',tmp,p]);raw=tmp.read_bytes();tmp.unlink()
    result={}
    for key,start,end in re.findall(r'(\w{4}): ([0-9a-fA-F]{8}) -> ([0-9a-fA-F]{8})',mp):
        a,b=int(start,16),int(end,16)
        result[key]=raw[a:b]
    return result,mp

def string_array(section):
    n=read32(section,4); data=section[8:]; values=data.split(b'\0')
    assert len(values)==n+1 and values[-1]==b''
    return values[:-1]

def text_extract(p,group):
    d=BASE/'editable/text'/group; d.mkdir(parents=True,exist_ok=True)
    orig=BASE/'originals/text'/group/p.name;orig.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,orig)
    sections,mp=ain_sections(orig)
    (orig.parent/'section-map.txt').write_text(mp,'utf8')
    text_info={}
    for key,kind in [('MSG0','messages'),('STR0','strings')]:
        vals=string_array(sections[key]); records=[]
        readable=[]
        for i,b in enumerate(vals):
            try: s=b.decode('gbk'); invalid=False
            except UnicodeDecodeError:s=b.decode('gbk','replace'); invalid=True
            records.append(dict(id=i,original=s,text=s,decode_warning=invalid))
            readable.append(f'[{i:06d}] {json.dumps(s,ensure_ascii=False)}')
        for i in range(0,len(records),1000):save_json(d/kind/f'{i:06d}-{min(i+999,len(records)-1):06d}.json',records[i:i+1000])
        (d/f'{kind}-readable.txt').write_text('\n'.join(readable)+'\n','utf8')
        text_info[kind]=dict(count=len(vals),decode_warnings=sum(r['decode_warning'] for r in records))
    # Native tool text contains function names and code context; its lines are commented by default.
    call(['ain','dump','-t','--input-encoding','GBK','-o',d/'by-function-reference.txt',orig])
    call(['ain','dump','-F','--input-encoding','GBK','-o',d/'source-filenames.txt',orig])
    info=dict(group=group,source=p.name,source_sha256=digest(orig),original=rel(orig),encoding='gbk',**text_info)
    save_json(d/'text-info.json',info)
    print('TEXT',group,text_info,flush=True)
    return info

def convert_one(im):
    raw=BASE/im['raw'];png=BASE/im['png']
    call(['cg','convert',raw,png])
    with Image.open(png) as p:
        p.load();rgba=p.convert('RGBA')
        if p.size!=(im['width'],im['height']):raise ValueError('Dimensions differ: '+str(png))
        im['alpha_sha256']=hashlib.sha256(rgba.getchannel('A').tobytes()).hexdigest()
        im['rgba_sha256']=hashlib.sha256(rgba.tobytes()).hexdigest()
    im['png_sha256']=digest(png)
    return im

def extract(workers):
    BASE.mkdir(exist_ok=True)
    if (BASE/'manifest.json').exists():raise ValueError('Extraction already exists; protect edits by using a new output workspace.')
    infos=[]
    for group,filename in GROUPS.items():infos.append(text_extract(GAME/filename,group))
    images=[]; archives=[]
    for p in sorted(GAME.glob('*.ald')):
        if p.stem[-2].upper()!='G':continue
        if p.name.lower() not in {'rance7ga.ald','rance7gb.ald','rance7gm.ald'}:continue
        group='steam_current'
        ims=ald_extract(p,group);images.extend(ims);archives.append(dict(group=group,file=p.name,count=len(ims)))
        print('NATIVE',p.name,len(ims),flush=True)
    sd=BASE/'originals/graphics/loose/NSPic';ed=BASE/'editable/graphics/loose/NSPic'
    sd.mkdir(parents=True,exist_ok=True);ed.mkdir(parents=True,exist_ok=True)
    for pi,p in enumerate(sorted((GAME/'NSPic').glob('*')),1):
        if p.suffix.lower()!='.qnt':continue
        raw=sd/p.name;shutil.copy2(p,raw);data=raw.read_bytes()
        images.append(dict(group='loose',archive='NSPic',pointer_index=pi,resource_ids=[],resource_ids_zero_based=[],
                           internal_name=p.name,raw=rel(raw),png=rel(ed/(p.stem+'.png')),
                           raw_sha256=hashlib.sha256(data).hexdigest(),**cgmetrics(data)))
    print('CONVERT TOTAL',len(images),'workers',workers,flush=True)
    success=[];errors=[];start=time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs={pool.submit(convert_one,im):im for im in images}
        for done,f in enumerate(as_completed(jobs),1):
            try:success.append(f.result())
            except Exception as e:errors.append(dict(image=jobs[f],error=str(e)))
            if done%500==0 or done==len(images):print('PNG',done,'/',len(images),'errors',len(errors),'seconds',round(time.time()-start),flush=True)
    success.sort(key=lambda im:(im['group'],im['archive'],im['pointer_index']))
    save_json(BASE/'manifest.json',dict(source_game=str(GAME),text=infos,archives=archives,images=success,errors=errors,
                                     total_images=len(images),converted_images=len(success)))
    write_gallery(json.loads((BASE/'manifest.json').read_text('utf8')))
    save_json(BASE/'extraction-errors.json',errors)
    print('DONE',len(success),'errors',len(errors),flush=True)
    if errors:raise RuntimeError('Incomplete extraction; see extraction-errors.json')

def encode_image(im,png,dst):
    raw=BASE/im['raw'];data=raw.read_bytes()
    if digest(raw)!=im['raw_sha256']:raise ValueError('Original asset changed: '+str(raw))
    with Image.open(png) as p:
        rgba=p.convert('RGBA')
        if p.size!=(im['width'],im['height']):raise ValueError('Keep original dimensions: '+str(png))
        alpha=rgba.getchannel('A')
        if not im['alpha'] and alpha.getextrema()!=(255,255):raise ValueError('Opaque source cannot gain alpha: '+str(png))
        if im['format']=='ajp':
            # Preserve proprietary PMS mask bytes; JPEG editing is intentionally lossy.
            if hashlib.sha256(alpha.tobytes()).hexdigest()!=im['alpha_sha256']:
                raise ValueError('AJP alpha changes need an AJP/PMS encoder; preserve alpha or use ALDExplorer.')
            jpg=io.BytesIO();rgba.convert('RGB').save(jpg,'JPEG',quality=95,subsampling=0)
            jpeg=bytearray(jpg.getvalue())
            for i in range(min(16,len(jpeg))):jpeg[i]^=KEY[i]
            off=read32(data,20);ms=read32(data,32);mo=read32(data,28)
            header=bytearray(data[:off]);struct.pack_into('<I',header,24,len(jpeg))
            struct.pack_into('<I',header,28,off+len(jpeg) if ms else 0)
            dst.write_bytes(header+jpeg+data[mo:mo+ms])
        else:
            tmp=dst.with_suffix('.converted.qnt');call(['cg','convert',png,tmp]);converted=tmp.read_bytes();tmp.unlink()
            nh=read32(converted,8);ps=read32(converted,36);az=read32(converted,40)
            shift=4 if read32(data,4) else 0;oh=read32(data,8) if shift else 48
            header=bytearray(data[:oh]);struct.pack_into('<I',header,32+shift,ps)
            struct.pack_into('<I',header,36+shift,az if im['alpha'] else 0)
            dst.write_bytes(header+converted[nh:nh+ps]+(converted[nh+ps:nh+ps+az] if im['alpha'] else b''))
    check=dst.with_suffix('.check.png');call(['cg','convert',dst,check])
    with Image.open(check) as p:
        decoded=p.convert('RGBA')
        if decoded.size!=rgba.size:raise ValueError('Converted image size mismatch')
        if im['format']=='qnt' and decoded.tobytes()!=rgba.tobytes():raise ValueError('QNT pixel mismatch')
        if im['format']=='ajp' and decoded.getchannel('A').tobytes()!=alpha.tobytes():raise ValueError('AJP alpha mismatch')
    check.unlink()

def rebuild_ald(meta,replacements,dst):
    prefix=bytearray((BASE/meta['prefix']).read_bytes());footer=(BASE/meta['footer']).read_bytes()
    def setptr(i,value):
        if value%256 or value>=2**32:raise ValueError('ALD alignment/size exceeds format limit')
        prefix[i*3:i*3+3]=(value//256).to_bytes(3,'little')
    with dst.open('wb') as f:
        f.write(prefix)
        for entry in meta['entries']:
            if 'alias_to' in entry:continue
            setptr(entry['pointer_index'],f.tell())
            hdr=bytearray(base64.b64decode(entry['header']))
            native=replacements.get(entry['pointer_index'],BASE/entry['raw'])
            data=native.read_bytes()
            if entry['pointer_index'] not in replacements and hashlib.sha256(data).hexdigest()!=entry['raw_sha256']:
                raise ValueError('Native originals changed: '+entry['raw'])
            struct.pack_into('<I',hdr,4,len(data));f.write(hdr);f.write(data)
            if entry['pointer_index'] in replacements:
                f.write(b'\0'*((-f.tell())%256))
            else:f.write(base64.b64decode(entry['padding']))
            assert f.tell()%256==0
        for entry in reversed(meta['entries']):
            if 'alias_to' in entry:setptr(entry['pointer_index'],u24(prefix[entry['alias_to']*3:entry['alias_to']*3+3])*256)
        setptr(len(meta['entries'])+1,f.tell());f.write(footer);f.seek(0);f.write(prefix)
    # Re-open every physical member; payloads, name/header metadata, mapping and footer must survive.
    with dst.open('rb') as f:
        rebuilt=f.read(len(prefix));map_start=u24(prefix[:3])*256
        assert rebuilt[map_start:]==prefix[map_start:]
        for entry in meta['entries']:
            if 'alias_to' in entry:
                a=entry['pointer_index']*3;b=entry['alias_to']*3
                assert rebuilt[a:a+3]==rebuilt[b:b+3]
                continue
            pi=entry['pointer_index'];f.seek(u24(rebuilt[pi*3:pi*3+3])*256)
            original_header=base64.b64decode(entry['header']);hs=len(original_header)
            hdr=f.read(hs);size=read32(hdr,4);b=f.read(size)
            assert hdr[:4]==original_header[:4] and hdr[8:]==original_header[8:]
            expected=digest(replacements[pi]) if pi in replacements else entry['raw_sha256']
            assert hashlib.sha256(b).hexdigest()==expected
        f.seek(-16,2);assert f.read()==footer

def changes(manifest):
    images=[im for im in manifest['images'] if digest(BASE/im['png'])!=im['png_sha256']]
    texts={};full={}
    for info in manifest['text']:
        group=info['group'];texts[group]=[];full[group]={}
        original,_=ain_sections(BASE/info['original'])
        for kind,key,letter in [('messages','MSG0','m'),('strings','STR0','s')]:
            vals=string_array(original[key]);seen={};edits=[]
            for p in sorted((BASE/'editable/text'/group/kind).glob('*.json')):
                for entry in json.loads(p.read_text('utf8')):
                    i=entry['id']
                    if type(i)!=int or not 0<=i<len(vals) or i in seen:raise ValueError('Invalid/duplicate text ID')
                    seen[i]=entry
                    orig=vals[i].decode('gbk','replace')
                    if entry['original']!=orig:raise ValueError('Do not edit original field')
                    s=entry['text']
                    if not isinstance(s,str):raise ValueError('Text must be a string')
                    if s!=orig:
                        if '\0' in s:raise ValueError('NUL not allowed in text')
                        s.encode('gbk',errors='strict');edits.append((i,s))
                        texts[group].append(dict(kind=letter,id=i,original=orig,text=s))
            if len(seen)!=len(vals):raise ValueError('Missing text IDs; do not delete rows/chunks')
            full[group][key]=(vals,edits)
    return images,texts,full

def build(output,scan=False):
    manifest=json.loads((BASE/'manifest.json').read_text('utf8'))
    output=Path(output).resolve()
    if not output.is_relative_to(BASE/'build') or output==BASE/'build':raise ValueError('Output must be a subdirectory of workspace/build')
    images,texts,full=changes(manifest)
    report=dict(image_changes=[dict(group=im['group'],archive=im['archive'],pointer_index=im['pointer_index'],png=im['png']) for im in images],
                text_changes=texts,outputs=[],validation=[])
    if scan:
        print(json.dumps({'image_changes':len(images),'text_changes':{k:len(v) for k,v in texts.items()}},ensure_ascii=False));return report
    if output.exists():raise ValueError('Use a new build output directory')
    output.mkdir(parents=True)
    encoded={}
    for im in images:
        native=output/'native'/im['group']/Path(im['raw']).parent.name/Path(im['raw']).name
        native.parent.mkdir(parents=True,exist_ok=True);encode_image(im,BASE/im['png'],native)
        encoded[(im['group'],im['archive'],im['pointer_index'])]=native
        if im['group']=='loose':
            target=output/'install/NSPic'/im['internal_name'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(native,target)
            report['outputs'].append(rel(target))
    targets=sorted(set((im['group'],im['archive']) for im in images if im['group']!='loose'))
    for group,archive in targets:
        meta=json.loads((BASE/'originals/archive_structure'/group/Path(archive).stem/'archive.json').read_text('utf8'))
        replacements={pi:path for (g,a,pi),path in encoded.items() if (g,a)==(group,archive)}
        dst=output/'install'/archive;dst.parent.mkdir(parents=True,exist_ok=True)
        rebuild_ald(meta,replacements,dst);report['outputs'].append(rel(dst));report['validation'].append('All ALD members verified: '+archive)
    for info in manifest['text']:
        group=info['group'];edits=texts[group]
        if not edits:continue
        orig=BASE/info['original']
        if digest(orig)!=info['source_sha256']:raise ValueError('Original AIN changed')
        # Native patch syntax is C-like, not JSON: reject unsupported control escapes before calling the tool.
        lines=[]
        for e in edits:
            s=e['text']
            if any(ord(c)<32 and c not in '\n\r\t' for c in s):raise ValueError('Unsupported control character')
            esc=s.replace('\\','\\\\').replace('"','\\"').replace('\n','\\n').replace('\r','\\r').replace('\t','\\t')
            lines.append(f'{e["kind"]}[{e["id"]}] = "{esc}"')
        patch=output/(group+'-patch.txt');patch.write_text('\n'.join(lines)+'\n','utf8')
        dst=output/'install'/info['source'];dst.parent.mkdir(parents=True,exist_ok=True)
        call(['ain','edit','--input-encoding','UTF-8','--output-encoding','GBK','-t',patch,'-o',dst,orig])
        after,_=ain_sections(dst);before,_=ain_sections(orig)
        for key,(values,table_edits) in full[group].items():
            expected=values.copy()
            for i,s in table_edits:expected[i]=s.encode('gbk')
            assert string_array(after[key])==expected, 'AIN changed unintended messages/strings'
        for key in before:
            if key not in ('MSG0','STR0'):assert after[key]==before[key], 'AIN non-text section changed: '+key
        report['outputs'].append(rel(dst));report['validation'].append('All text and non-text AIN sections verified: '+info['source'])
    report['built_sha256']={r:digest(BASE/r) for r in report['outputs']}
    save_json(output/'build-report.json',report)
    print('BUILD',len(report['outputs']),'files; all section/member checks passed',flush=True)
    return report

def main():
    global BASE, GAME, ALICE
    location = argparse.ArgumentParser(add_help=False)
    location.add_argument('--workspace', type=Path)
    location.add_argument('--game', type=Path)
    location_args, remaining = location.parse_known_args()
    if location_args.workspace:
        BASE = location_args.workspace.resolve()
    if location_args.game:
        GAME = location_args.game.resolve()
    ALICE = app_root() / 'tools' / 'alice.exe'
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('extract');p.add_argument('--workers',type=int,default=8)
    p=sub.add_parser('scan')
    p=sub.add_parser('build');p.add_argument('--output',required=True)
    args=parser.parse_args(remaining)
    if args.command=='extract':extract(args.workers)
    else:build(BASE/'build/scan' if args.command=='scan' else args.output,scan=args.command=='scan')

if __name__=='__main__':main()
