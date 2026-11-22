#!/usr/bin/env python3
"""Build the Corso C/F learning editions from documented melody transcriptions.

The right-hand route is chosen against the exact 21-button instrument map.
Left-hand pulses are sounded only while their bellows direction matches the
melody. No modern protected melody is part of this pipeline.
"""
from __future__ import annotations

import copy
import datetime
import html
import io
import json
import math
import re
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
import cairosvg
import mido
import verovio
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = Path(__file__).resolve().parents[1]
LAYOUT = json.loads((ROOT / 'instrument/corso-cf.json').read_text())
REPO_URL = 'https://github.com/hailbagel/Akkordeon'
MEI = 'http://www.music-encoding.org/ns/mei'
XMLID = '{http://www.w3.org/XML/1998/namespace}id'
ORDER = ['alle-jahre-wieder','schneefloeckchen','leise-rieselt-der-schnee',
         'ihr-kinderlein-kommet','o-tannenbaum','lasst-uns-froh','morgen-kinder',
         'jingle-bells','stille-nacht-heilige-nacht','o-du-froehliche',
         'we-wish-you-a-merry-christmas','suesser-die-glocken',
         'kling-gloeckchen','froehliche-weihnacht']
NAT = {'C':0,'D':2,'E':4,'F':5,'G':7,'A':9,'B':11}
FLAT_NAMES = ['C','Db','D','Eb','E','F','F#','G','G#','A','Bb','B']
CHORD_NAMES = ['C','Db','D','Eb','E','F','F#','G','Ab','A','Bb','B']
AVAILABLE = {'C','F','G','A','Dm','Bb'}
VOICINGS = {'C':['C3','E3','G3'],'F':['F2','A2','C3'],
            'G':['G2','B2','D3'],'A':['A2','C#3','E3'],
            'Dm':['D3','F3','A3'],'Bb':['Bb2','D3','F3']}
BASS_PITCHES = {'C':'C3','F':'F2','G':'G2','A':'A2','D':'D3','Bb':'Bb2'}
BEATS = {4:('whole',0),3:('half',1),2:('half',0),1.5:('quarter',1),
         1:('quarter',0),.75:('eighth',1),.5:('eighth',0),.375:('16th',1),.25:('16th',0)}
DUR = {'whole':'1','half':'2','quarter':'4','eighth':'8','16th':'16'}

def note_parts(p):
    m = re.fullmatch(r'([A-G])([#b]?)([0-9])',p)
    if not m: raise ValueError(f'Invalid pitch: {p}')
    l,a,o=m.groups()
    return l,a,int(o)

def midi(p):
    l,a,o=note_parts(p)
    return (o+1)*12+NAT[l]+{'':0,'#':1,'b':-1}[a]

def pitch(n):
    return FLAT_NAMES[n%12]+str(n//12-1)

def pc(s):
    m=re.match(r'^([A-G])([#b]?)',s)
    if not m: raise ValueError(f'Invalid chord/key: {s}')
    return (NAT[m[1]]+{'':0,'#':1,'b':-1}[m[2]])%12

def transpose_chord(ch,shift):
    ch=ch.split('/')[0].replace('♭','b').replace('♯','#').replace('H','B')
    m=re.match(r'^([A-G][#b]?)(.*)',ch)
    if not m: return None
    suffix=m[2]
    minor=suffix.startswith('m') and not suffix.startswith('maj')
    return CHORD_NAMES[(pc(m[1])+shift)%12]+('m' if minor else '')

def chord_pcs(ch):
    root=pc(ch)
    return {root,(root+(3 if ch.endswith('m') else 4))%12,(root+7)%12}

def available_left(ch,direction,kind):
    label=ch if kind=='chord' else re.match(r'[A-G][#b]?',ch).group()
    return next((b['button'] for b in LAYOUT['bass'] if b['kind']==kind and b[direction]==label),None)

def choose_harmony(notes,key,previous=None):
    pool=['C','F','G','Dm'] if key=='C' else ['F','Bb','C','Dm']
    def cost(ch):
        tones=chord_pcs(ch)
        return sum(e['duration']*(0 if midi(e['pitch'])%12 in tones else 1)
                   *(1.5 if i==0 else 1) for i,e in enumerate(notes) if e['pitch']!='rest') \
               + (0 if ch==previous else .12) + (.12 if ch=='Dm' else 0)
    return min(pool,key=cost)

def harmony_at(m,offset):
    result=m['harmony'][0]['chord']
    for h in m['harmony']:
        if h['offset']<=offset+1e-8: result=h['chord']
    return result

def make_candidate(source,key,shift):
    measures=[]; start=0.; previous=None; adaptations=[]
    for mi,sm in enumerate(source['measures']):
        events=[]; off=0.
        for ei,se in enumerate(sm['events']):
            p='rest' if se['pitch']=='rest' else pitch(midi(se['pitch'])+shift)
            events.append({'id':f"{source['id']}-m{mi}-e{ei}",'pitch':p,'midi':None if p=='rest' else midi(p),
                           'duration':float(se['duration']),'offset':off,'start':start+off})
            off+=float(se['duration'])
        changes=sm.get('chord_events') or ([{'offset':0,'chord':sm['chord']}] if sm.get('chord') else [])
        changes=sorted(changes,key=lambda x:x['offset'])
        if not changes or changes[0]['offset']!=0:
            changes.insert(0,{'offset':0,'chord':sm.get('chord')})
        harmony=[]
        for hi,h in enumerate(changes):
            end=changes[hi+1]['offset'] if hi+1<len(changes) else off
            segment=[e for e in events if e['offset']<end and e['offset']+e['duration']>h['offset']]
            original=transpose_chord(h['chord'],shift) if h['chord'] else None
            chosen=original if original in AVAILABLE else choose_harmony(segment,key,previous)
            harmony.append({'offset':float(h['offset']),'chord':chosen,'source_chord':h['chord']})
            if original and chosen!=original: adaptations.append({'measure':mi,'from':original,'to':chosen})
            previous=chosen
        number=mi if source.get('pickup_quarters',0)>0 else mi+1
        measures.append({'number':number,'start':start,'duration':off,'events':events,'harmony':harmony})
        start+=off
    options={}
    for b in LAYOUT['melody']:
        for direction in ['D','Z']:
            options.setdefault(midi(b[direction]),[]).append({'button':b['button'],'row':b['row'],
                                                             'position':b['position'],'dir':direction})
    route=[]
    for m in measures:
        for e in m['events']:
            if e['midi'] is not None:
                choices=options.get(e['midi'],[])
                if not choices:return None
                route.append((e,choices,harmony_at(m,e['offset'])))
            else:e.update(button=None,row=None,dir=None)
    states=[]
    for index,(e,choices,ch) in enumerate(route):
        layer=[]
        for opt in choices:
            local=(0 if available_left(ch,opt['dir'],'chord') else 1.6)*min(e['duration'],1.5)
            local+=.07*abs(opt['position']-5)+(.15 if opt['position']<3 else 0)
            if index==0: layer.append((local,None));continue
            pe,previous_choices,_=route[index-1]
            costs=[]
            for j,prev in enumerate(previous_choices):
                changing=opt['dir']!=prev['dir']
                speed_factor=1.0 if pe['duration']>=.5 else 1.8
                movement=.11*abs(opt['position']-prev['position'])
                switch=.65*(opt['row']!=prev['row'])
                costs.append(states[-1][j][0]+local+movement+switch+1.1*changing*speed_factor)
            j=min(range(len(costs)),key=costs.__getitem__)
            layer.append((costs[j],j))
        states.append(layer)
    j=min(range(len(states[-1])),key=lambda x:states[-1][x][0])
    total_cost=states[-1][j][0]
    for i in range(len(route)-1,-1,-1):
        e,choices,_=route[i];e.update(choices[j]);j=states[i][j][1]
    # Simple oom-pah accompaniment. Each pulse stops before a bellows reversal.
    meter=source['meter'];bar_quarters=int(meter.split('/')[0])*4/int(meter.split('/')[1])
    pulse=1.5 if meter=='6/8' else 1.0
    available_count=0;attempt_count=0
    for mi,m in enumerate(measures):
        m['bass']=[]
        if mi==0 and source.get('pickup_quarters',0)>0:continue
        t=0.;pi=0
        while t<m['duration']-1e-8:
            window=min(pulse,m['duration']-t)
            sounding=next((e for e in m['events'] if e['offset']<=t+1e-8 and e['offset']+e['duration']>t+1e-8),None)
            ch=harmony_at(m,t)
            kind='bass' if (pi==0 or (meter=='4/4' and pi==2)) else 'chord'
            attempt_count+=1
            if sounding and sounding['dir']:
                direction=sounding['dir'];button=available_left(ch,direction,kind)
                duration=window
                for later in m['events']:
                    if t<later['offset']<t+duration-1e-8 and later['dir'] not in [direction,None]:
                        duration=later['offset']-t;break
                for later in m['harmony']:
                    if t<later['offset']<t+duration-1e-8:duration=later['offset']-t;break
                if button and duration>=.25-1e-8:
                    root=re.match(r'[A-G][#b]?',ch).group()
                    pitches=VOICINGS[ch] if kind=='chord' else [BASS_PITCHES[root]]
                    m['bass'].append({'start':m['start']+t,'offset':t,'duration':duration,'button':button,
                                      'dir':direction,'kind':kind,'chord':ch,'pitches':pitches,
                                      'midis':[midi(p) for p in pitches]})
                    available_count+=1
            t+=window;pi+=1
    pitches=[e['midi'] for m in measures for e in m['events'] if e['midi'] is not None]
    reversals=sum(a[0]['dir']!=b[0]['dir'] for a,b in zip(route,route[1:]))
    # Preference for the comfortable middle register and for available bass pulses.
    score=total_cost/max(1,len(route)) + abs(sum(pitches)/len(pitches)-70)*.025 \
          + (1-available_count/max(1,attempt_count))*.55 \
          + sum(max(0,n-77) for n in pitches)/len(pitches)*.06
    return {'id':source['id'],'title':source['title'],'key':key,'source_key':source['key'],
            'transposition_semitones':shift,'meter':meter,'tempo_bpm':source['tempo_bpm'],
            'practice_tempo_bpm':min(source['tempo_bpm'],60 if meter=='6/8' else 66),
            'difficulty':source['difficulty'],'learning_order':ORDER.index(source['id'])+1,
            'pickup_quarters':source.get('pickup_quarters',0),'total_quarters':start,'measures':measures,
            'practice_notes_de':source.get('practice_notes_de',[]),'source_urls':source['source_urls'],
            'composer':source.get('composer','Traditional'),'adaptations':adaptations,
            'bellows_reversals':reversals,'bass_pulses':available_count,
            'bass_pulses_omitted':attempt_count-available_count,
            'range':[pitch(min(pitches)),pitch(max(pitches))],
            'score_path':f"../songs/public-domain/{source['id']}/score.pdf",'_cost':score}

def arrange(source):
    candidates=[]
    for key in ['C','F']:
        base=(pc(key)-pc(source['key'])+6)%12-6
        for octave in [-12,0,12]:
            candidate=make_candidate(source,key,base+octave)
            if candidate:candidates.append(candidate)
    if not candidates:raise ValueError(f"No playable transposition: {source['id']}")
    best=min(candidates,key=lambda c:c['_cost']);best.pop('_cost')
    # Practice advice describes this edition, independent of source transposition.
    if source['id']=='ihr-kinderlein-kommet':
        best['practice_notes_de'][-1]='Die letzten drei Takte langsam üben: den tiefen Leitton unmittelbar vor dem Schlusston gesondert suchen.'
    if source['id']=='kling-gloeckchen':
        best['practice_notes_de'][-1]='Im Mittelteil den Ton mit dem zusätzlichen Vorzeichen gesondert suchen; erst danach die Bassbegleitung ergänzen.'
    return best

def splits(duration):
    # Compound 6/8 phrase tails retain their dotted-quarter + quarter spelling.
    if abs(duration-2.5)<1e-8:return [1.5,1.]
    result=[]
    while duration>1e-8:
        d=max(d for d in BEATS if d<=duration+1e-8)
        result.append(d);duration-=d
    return result

def sub(parent,tag,text=None,**attrs):
    el=ET.SubElement(parent,tag,{k:str(v) for k,v in attrs.items()})
    if text is not None:el.text=str(text)
    return el

def bass_sequence(m):
    result=[];t=0.
    for b in m['bass']:
        if b['offset']>t+1e-8:result.append({'duration':b['offset']-t,'pitches':[],'offset':t})
        result.append(b);t=b['offset']+b['duration']
    if t<m['duration']-1e-8:result.append({'duration':m['duration']-t,'pitches':[],'offset':t})
    return result

def musicxml(song):
    root=ET.Element('score-partwise',version='4.0')
    work=sub(root,'work');sub(work,'work-title',song['title'])
    ident=sub(root,'identification');sub(ident,'creator',song['composer'],type='composer')
    sub(ident,'rights','New C/F learning arrangement; sources and rights in source.json and README.md.')
    defaults=sub(root,'defaults');sc=sub(defaults,'scaling');sub(sc,'millimeters',7);sub(sc,'tenths',40)
    pl=sub(root,'part-list');sp=sub(pl,'score-part',id='P1');sub(sp,'part-name','Corso C/F')
    part=sub(root,'part',id='P1')
    for mi,m in enumerate(song['measures']):
        measure=sub(part,'measure',number=m['number'],implicit='yes' if m['duration']!=int(song['meter'][0])*4/int(song['meter'][-1]) else 'no')
        if mi==0:
            a=sub(measure,'attributes');sub(a,'divisions',8)
            k=sub(a,'key');sub(k,'fifths',0 if song['key']=='C' else -1)
            tm=sub(a,'time');num,den=song['meter'].split('/');sub(tm,'beats',num);sub(tm,'beat-type',den)
            sub(a,'staves',2)
            for n,sign,ln in [(1,'G',2),(2,'F',4)]:
                cl=sub(a,'clef',number=n);sub(cl,'sign',sign);sub(cl,'line',ln)
            d=sub(measure,'direction',placement='above');dt=sub(d,'direction-type');met=sub(dt,'metronome')
            sub(met,'beat-unit','quarter');sub(met,'per-minute',song['practice_tempo_bpm']);sub(d,'sound',tempo=song['practice_tempo_bpm'])
        for change in m['harmony']:
            h=sub(measure,'harmony');r=sub(h,'root')
            rootname=re.match(r'[A-G][#b]?',change['chord']).group()
            sub(r,'root-step',rootname[0])
            if len(rootname)>1:sub(r,'root-alter',1 if rootname[1]=='#' else -1)
            sub(h,'kind','minor' if change['chord'].endswith('m') else 'major')
            sub(h,'offset',round(change['offset']*8));sub(h,'staff',1)
        for staffnum,events in [(1,m['events']),(2,bass_sequence(m))]:
            if staffnum==2:sub(sub(measure,'backup'),'duration',round(m['duration']*8))
            for ei,e in enumerate(events):
                pitches=([e['pitch']] if e.get('pitch') not in [None,'rest'] else []) if staffnum==1 else e['pitches']
                chunks=splits(e['duration'])
                for ci,dur in enumerate(chunks):
                    for ni,p in enumerate(pitches or [None]):
                        note=sub(measure,'note',id=e.get('id',f'b{mi}-{ei}')+f'-{ci}-{ni}')
                        if ni:sub(note,'chord')
                        if p:
                            l,a,o=note_parts(p);pe=sub(note,'pitch');sub(pe,'step',l)
                            if a:sub(pe,'alter',1 if a=='#' else -1)
                            sub(pe,'octave',o)
                        else:sub(note,'rest')
                        sub(note,'duration',round(dur*8))
                        if p and ci>0:sub(note,'tie',type='stop')
                        if p and ci<len(chunks)-1:sub(note,'tie',type='start')
                        sub(note,'voice',staffnum)
                        typ,dots=BEATS[dur];sub(note,'type',typ)
                        if dots:sub(note,'dot')
                        sub(note,'staff',staffnum)
                        if p and len(chunks)>1:
                            nots=sub(note,'notations')
                            if ci>0:sub(nots,'tied',type='stop')
                            if ci<len(chunks)-1:sub(nots,'tied',type='start')
                        if p and not ni and not ci:
                            label=f"{e['button']}{e['dir']}"
                            sub(sub(note,'lyric',number='1'),'text',label)
                            label2=e['pitch'] if staffnum==1 else e['chord']
                            sub(sub(note,'lyric',number='2'),'text',label2)
        if mi==len(song['measures'])-1:sub(sub(measure,'barline',location='right'),'bar-style','light-heavy')
    ET.indent(root,space='  ')
    return '<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(root,encoding='unicode')+'\n'

def mei_score(song):
    # MEI gives explicit, stable IDs to the browser score and preserves the two annotation rows.
    root=ET.Element('mei',xmlns=MEI,meiversion='5.0')
    mh=sub(root,'meiHead');fd=sub(mh,'fileDesc');ts=sub(fd,'titleStmt');sub(ts,'title',song['title']);sub(fd,'pubStmt')
    score=sub(sub(sub(sub(root,'music'),'body'),'mdiv'),'score')
    num,den=song['meter'].split('/')
    sd=sub(score,'scoreDef',**{'meter.count':num,'meter.unit':den,'key.sig':'0' if song['key']=='C' else '1f'})
    sg=sub(sd,'staffGrp',symbol='brace',**{'bar.thru':'false'})
    for n,clef,ln in [(1,'G',2),(2,'F',4)]:
        sub(sg,'staffDef',n=n,lines=5,**{'clef.shape':clef,'clef.line':ln})
    section=sub(score,'section')
    for mi,m in enumerate(song['measures']):
        if song['id']=='froehliche-weihnacht':
            if mi in [12,20]:sub(section,'pb')
            elif mi in [3,6,9,15,18,23,26,29]:sub(section,'sb')
        attrs={'n':str(m['number']),XMLID:f"{song['id']}-measure-{mi}"}
        if m['duration']!=int(num)*4/int(den):attrs['metcon']='false'
        if mi==len(song['measures'])-1:attrs['right']='end'
        me=ET.SubElement(section,'measure',attrs);ties=[]
        for staffnum,events in [(1,m['events']),(2,bass_sequence(m))]:
            st=sub(me,'staff',n=staffnum);lay=sub(st,'layer',n=1)
            state={}
            for ei,e in enumerate(events):
                ps=([e['pitch']] if e.get('pitch') not in [None,'rest'] else []) if staffnum==1 else e['pitches']
                chunks=splits(e['duration']);previous_ids=[]
                for ci,dur in enumerate(chunks):
                    typ,dots=BEATS[dur];durattrs={'dur':DUR[typ]}
                    if dots:durattrs['dots']=dots
                    group=lay if len(ps)<=1 else sub(lay,'chord',**durattrs)
                    ids=[];first_note=None
                    for ni,p in enumerate(ps or [None]):
                        nid=e.get('id',f"{song['id']}-b{mi}-{ei}")+f'-{ci}-{ni}'
                        if not p:
                            sub(lay,'rest',**durattrs);continue
                        l,a,o=note_parts(p)
                        attrs={'pname':l.lower(),'oct':o,XMLID:nid}
                        if len(ps)<=1:attrs.update(durattrs)
                        actual={'':'n','#':'s','b':'f'}[a]
                        default='f' if song['key']=='F' and l=='B' else 'n'
                        if actual!=state.get((l,o),default):attrs['accid']=actual
                        attrs['accid.ges']=actual
                        state[(l,o)]=actual
                        ne=sub(group,'note',**attrs);ids.append(nid)
                        if first_note is None:first_note=ne
                    if ps and ci==0:
                        label=f"{e['button']}{e['dir']}"
                        v=sub(first_note,'verse',n=1);sub(v,'syl',label)
                        v=sub(first_note,'verse',n=2);sub(v,'syl',e['pitch'] if staffnum==1 else e['chord'])
                    if previous_ids:
                        ties.extend(zip(previous_ids,ids))
                    previous_ids=ids
            # Beam short notes by beat so beginners can read rhythmic groups.
            children=list(lay);groups=[];group=[];last_beat=None;offset=0.
            beat=1.5 if song['meter']=='6/8' else 1.0
            for child in children:
                duration=4/int(child.attrib['dur'])*(1.5 if child.attrib.get('dots') else 1)
                current=int((offset+1e-8)/beat)
                short=child.tag in ['note','chord'] and duration<1
                if not short or current!=last_beat:
                    if group:groups.append(group)
                    group=[]
                if short:group.append(child)
                else:groups.append([child])
                last_beat=current;offset+=duration
            if group:groups.append(group)
            for child in children:lay.remove(child)
            for group in groups:
                parent=sub(lay,'beam') if len(group)>1 else lay
                for child in group:parent.append(child)
        for a,b in ties:sub(me,'tie',startid='#'+a,endid='#'+b)
        for h in m['harmony']:
            sub(me,'harm',h['chord'],staff=1,tstamp=1+h['offset']*(int(den)/4),place='above')
    return '<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(root,encoding='unicode')+'\n'

def register_fonts():
    candidates=[Path('/usr/share/fonts/truetype/dejavu'),Path('/usr/local/share/fonts')]
    for folder in candidates:
        if (folder/'DejaVuSans.ttf').exists():
            pdfmetrics.registerFont(TTFont('Body',str(folder/'DejaVuSans.ttf')))
            pdfmetrics.registerFont(TTFont('Bold',str(folder/'DejaVuSans-Bold.ttf')))
            return
    # A standard PDF font fallback keeps rebuilding possible on other systems.
    from reportlab.pdfbase.pdfmetrics import Font
    pdfmetrics.registerFont(Font('Body','Helvetica','WinAnsiEncoding'))
    pdfmetrics.registerFont(Font('Bold','Helvetica-Bold','WinAnsiEncoding'))

def overlay(song,page,total):
    buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=A4);w,h=A4
    c.setFillColor(HexColor('#18323D'));c.setFont('Bold',17)
    title=song['title'];size=17
    while pdfmetrics.stringWidth(title,'Bold',size)>w-72:size-=.5
    c.setFont('Bold',size);c.drawString(36,h-38,title)
    c.setFont('Body',9);c.drawString(36,h-55,f"HOHNER CORSO C/F  ·  {song['key']}-Dur  ·  {song['meter']}  ·  Lernfolge {song['learning_order']:02d}")
    c.setFillColor(HexColor('#536571'));c.setFont('Body',8.3)
    c.drawString(36,h-72,f"Starttempo: Viertel = {song['practice_tempo_bpm']}  |  Zahlen = Knöpfe, keine Finger  |  D = Druck, Z = Zug")
    c.drawString(36,h-85,'Oben: Melodie + Knopf/Balg + Tonname. Unten: optionale einfache Bassbegleitung.')
    c.setStrokeColor(HexColor('#138589'));c.setLineWidth(.8);c.line(36,h-94,w-36,h-94)
    c.setFillColor(HexColor('#536571'));c.setFont('Body',7.3)
    c.drawString(36,48,'B = deutsches H; Bb = deutsches B. C4 = mittleres C. Bassnoten in schematischer Leselage.')
    c.drawString(36,36,'Basspausen sind beabsichtigt: Beim Richtungswechsel zuerst den Bass loslassen. Melodie zuerst allein üben.')
    c.setFont('Body',7);c.drawString(36,23,f"Quellen, Lernschritte und Rechte: songs/public-domain/{song['id']}/README.md")
    c.drawRightString(w-36,12,f"{page}/{total} · 2026-11-22")
    c.save();buf.seek(0);return fitz.open(stream=buf.read(),filetype='pdf')

def engrave(song,folder):
    xml=musicxml(song);mei=mei_score(song)
    for old in folder.glob('score-[0-9][0-9].svg'):old.unlink()
    (folder/'score.musicxml').write_text(xml)
    (folder/'score.mei').write_text(mei)
    # A slightly smaller staff prevents isolated closing measures on these pieces.
    width=1950 if song['id'] in ['morgen-kinder','we-wish-you-a-merry-christmas','o-du-froehliche','froehliche-weihnacht'] else 1800
    ratio=width/1800
    tk=verovio.toolkit();tk.setOptions({'inputFrom':'mei','scale':40,'pageWidth':width,'pageHeight':round(2546*ratio),
        'pageMarginTop':round(320*ratio),'pageMarginBottom':round(205*ratio),'pageMarginLeft':round(110*ratio),'pageMarginRight':round(110*ratio),
        'header':'none','footer':'none','font':'Bravura','fontTextLiberation':False,'smuflTextFont':'none',
        'spacingStaff':14,'spacingSystem':6,'lyricSize':4.8,'lyricTopMinMargin':3,'lyricWordSpace':1.7,
        'breaks':'encoded' if song['id']=='froehliche-weihnacht' else 'auto','breaksNoWidow':True,
        'justifyVertically':False,'minLastJustification':.5,'xmlIdChecksum':True})
    if not tk.loadData(mei):raise RuntimeError(f"Cannot engrave {song['id']}: {tk.getLog()}")
    pdf=fitz.open();svg_pages=[]
    for i in range(1,tk.getPageCount()+1):
        svg=tk.renderToSVG(i);svg_pages.append(svg)
        (folder/f'score-{i:02d}.svg').write_text(svg)
        # CairoSVG resolves Verovio's nested SVG viewport and embedded glyph paths.
        # MuPDF's SVG importer omits that nested viewport and produces blank music.
        vector=fitz.open(stream=cairosvg.svg2pdf(bytestring=svg.encode()),filetype='pdf')
        out=pdf.new_page(width=A4[0],height=A4[1]);out.show_pdf_page(out.rect,vector,0)
        label=overlay(song,i,tk.getPageCount());out.show_pdf_page(out.rect,label,0)
    pdf.set_metadata({'title':song['title']+' — Hohner Corso C/F','author':'Akkordeon learning collection',
                      'subject':'Original beginner arrangement with verified button and bellows mapping'})
    pdf.save(folder/'score.pdf',garbage=4,deflate=True)
    song['svg_pages']=svg_pages;song['page_count']=len(svg_pages)
    return tk.getLog()

def write_midi(song,folder):
    mf=mido.MidiFile(ticks_per_beat=480)
    for trackno in range(2):
        track=mido.MidiTrack();mf.tracks.append(track)
        track.append(mido.MetaMessage('track_name',name='Melody' if trackno==0 else 'Optional bass'))
        track.append(mido.Message('program_change',channel=trackno,program=21 if trackno==0 else 22,time=0))
        if trackno==0:track.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(song['practice_tempo_bpm']),time=0))
        ev=[]
        for m in song['measures']:
            for e in m['events'] if trackno==0 else m['bass']:
                ns=([e['midi']] if e['midi'] is not None else []) if trackno==0 else e['midis']
                for n in ns:
                    ev.extend([(round(e['start']*480),1,n),(round((e['start']+e['duration']*.9)*480),0,n)])
        last=0
        for t,on,n in sorted(ev):
            track.append(mido.Message('note_on' if on else 'note_off',channel=trackno,note=n,velocity=(72 if trackno==0 else 48) if on else 0,time=t-last));last=t
        track.append(mido.MetaMessage('end_of_track',time=round(song['total_quarters']*480)-last))
    mf.save(folder/'practice.mid')

def song_readme(song,source):
    title=source['title'];i=song['learning_order']
    row_counts={r:sum(e.get('row')==r for m in song['measures'] for e in m['events']) for r in ['C','F']}
    notes='\n'.join('- '+s for s in song['practice_notes_de'])
    sources='\n'.join(f'- {u}' for u in dict.fromkeys(source['source_urls']+source.get('rights_evidence_urls',[])))
    src_note=source.get('source_note','')
    return f'''# {i:02d} · {title}

[← Übersicht](../../../README.md) · [PDF-Noten](score.pdf) · [MusicXML bearbeiten](score.musicxml) · [MIDI üben](practice.mid) · [Offline-Player](../../../practice/index.html)

## Für deine Hohner Corso C/F

| Punkt | Lernfassung |
|---|---|
| Tonart / Takt | {song['key']}-Dur · {song['meter']} |
| Langsames Starttempo | Viertel = {song['practice_tempo_bpm']} |
| Tonumfang | {song['range'][0]} bis {song['range'][1]} |
| Takte einschließlich Auftakt | {len(song['measures'])} |
| Melodietöne auf den Reihen | C-Reihe: {row_counts['C']} · F-Reihe: {row_counts['F']} |
| Bassbegleitung | {song['bass_pulses']} spielbare Einsätze; {song['bass_pulses_omitted']} bewusst ausgelassene Einsätze |

**Knopfzahlen sind keine Fingersätze.** `14D` heißt Knopf 14 auf Druck, `5Z` heißt Knopf 5 auf Zug. Melodieknöpfe: 1–11 außen/C, 12–21 innen/F; vom Kinn zum Knie zählen. `L1`–`L8` folgen dem [Bassschema](../../../instrument/README.md). Internationale Namen: B = H, Bb = deutsches B.

## In drei Schritten lernen

1. **Melodie allein:** Zwei Takte im Player auswählen, Bass ausschalten und auf 50–65 % des späteren Tempos spielen. Knöpfe und Balgrichtung zuerst sicher treffen.
2. **Rhythmus festigen:** Dieselbe Stelle dreimal ohne Unterbrechung spielen. Danach die nächsten zwei Takte anhängen. Einen Auftakt nicht als vollständigen Takt mitzählen.
3. **Bass hinzufügen:** Nur die untere Notenzeile ergänzen. Basspausen gehören zur Lernfassung. Bei einem Balgwechsel den Bass vorher loslassen. Erst dann das Tempo in kleinen Schritten erhöhen.

{notes}

Die Begleitung ist eine neue, vereinfachte Lernbegleitung. Septakkorde sind auf vorhandene Dreiklänge reduziert; weitere Harmonien wurden bei Bedarf vereinfacht. Sie ist auf die notierte Melodie- und Balgführung abgestimmt. Die Bassoktaven sind eine Leselage, keine Behauptung über alle tatsächlich klingenden Stimmchöre.

## Quellen und Bearbeitung

- Tonart der Quelle: {source['key']}; Lernfassung: {song['key']}; Transposition: {song['transposition_semitones']:+d} Halbtöne.
- Melodie: {source.get('composer','Traditional')}.
- Historische Textzuordnung (Liedtext nicht enthalten): {source.get('lyricist','Traditional')}.
- Herkunft und ausgewählte Fassung: {src_note}
- Eigene Melodietranskription und neue Lernbegleitung für die Corso C/F. Kein Nachdruck einer modernen käuflichen Ausgabe.
- Maschinenlesbare Quelle und Bearbeitung: [source.json](source.json), [arrangement.json](arrangement.json).

### Belege und Quellenlinks

{sources}

## Notenvorschau

![Score, page 1](score-01.svg)
'''

def songbook_cover(songs):
    buf=io.BytesIO();c=canvas.Canvas(buf,pagesize=A4);w,h=A4
    c.setFillColor(HexColor('#18323D'));c.setFont('Bold',26)
    c.drawString(40,h-58,'Weihnachten auf der Corso')
    c.setFont('Body',12);c.drawString(40,h-81,'14 Lernfassungen für die Hohner Corso C/F · 21 Melodieknöpfe / 8 Bässe')
    c.setFillColor(HexColor('#138589'));c.rect(40,h-100,w-80,3,fill=1,stroke=0)
    c.setFillColor(HexColor('#18323D'));c.setFont('Bold',12);c.drawString(40,h-128,'So beginnst du')
    c.setFont('Body',9.5)
    for i,line in enumerate([
        '1. Erst die Melodie allein: mit „Alle Jahre wieder“ beginnen und zwei Takte wiederholen.',
        '2. Knopfzahl und Balgrichtung lesen: 14Z = Knopf 14 auf Zug; 14D = derselbe auf Druck.',
        '3. Die untere Basszeile erst ergänzen, wenn die Melodie sicher läuft. Pausen beachten.'
    ]):c.drawString(40,h-147-i*17,line)
    y=h-220;c.setFont('Bold',11);c.drawString(40,y,'Lernfolge und Inhaltsverzeichnis');y-=24
    c.setFillColor(HexColor('#EAF3F2'));c.rect(40,y-6,w-80,21,fill=1,stroke=0)
    c.setFillColor(HexColor('#18323D'));c.setFont('Bold',9)
    for x,t in [(47,'Nr.'),(79,'Lied'),(400,'Tonart'),(462,'Stufe'),(515,'Seite')]:c.drawString(x,y,t)
    page=4;y-=23;c.setFont('Body',9)
    for s in songs:
        c.drawString(47,y,f"{s['learning_order']:02d}");c.drawString(79,y,s['title'])
        c.drawString(405,y,s['key']);c.drawString(472,y,str(s['difficulty']));c.drawRightString(541,y,str(page))
        c.setStrokeColor(HexColor('#D9E4E5'));c.setLineWidth(.4);c.line(40,y-7,w-40,y-7)
        y-=22;page+=s['page_count']
    y-=18;c.setFont('Bold',10);c.drawString(40,y,'Vor den Liedern: die vollständige Tonübersicht');y-=17
    c.setFont('Body',9);c.drawString(40,y,'Seite 2: alles auf Zug. Seite 3: alles auf Druck. L1–L8 siehe Bassschema im Repository.')
    y-=32;c.setFont('Bold',10);c.drawString(40,y,'Legende');y-=17;c.setFont('Body',9)
    for line in [
        'Stufe 1 = Einstieg; 2 = Aufbau; 3 = später lernen. Das ist eine Lernempfehlung.',
        'C-Reihe außen: 1–11. F-Reihe innen: 12–21. Jeweils vom Kinn zum Knie zählen.',
        'Internationale Tonnamen: B = deutsches H; Bb = deutsches B; C4 = mittleres C.',
        'Knopfzahlen sind keine Fingerzahlen. Bassoktaven sind nur eine schematische Leselage.'
    ]:c.drawString(40,y,line);y-=15
    c.setFillColor(HexColor('#536571'));c.setFont('Body',8)
    c.drawString(40,74,'Eigene Lernbearbeitungen historischer Melodien ohne Liedtexte. Quellen und Rechte im Repository.')
    c.drawString(40,60,'Acht weitere gewünschte Pop-Titel: Lernseiten und autorisierte Bezugsquellen, keine Vollnoten in diesem Heft.')
    c.setFont('Bold',9);c.drawString(40,38,'github.com/hailbagel/Akkordeon')
    c.linkURL(REPO_URL,(40,34,280,48),relative=0)
    c.setFont('Body',8);c.drawRightString(w-40,38,'Ausgabe 22.11.2026 · PDF + MIDI + Offline-Player')
    c.showPage();c.save();return fitz.open(stream=buf.getvalue(),filetype='pdf')

def main():
    register_fonts();songs=[];warnings=[]
    for sp in sorted((ROOT/'songs/public-domain').glob('*/source.json'),key=lambda p:ORDER.index(p.parent.name)):
        source=json.loads(sp.read_text());song=arrange(source);folder=sp.parent
        log=engrave(song,folder)
        if log:warnings.append({'song':song['id'],'log':log})
        write_midi(song,folder)
        (folder/'README.md').write_text(song_readme(song,source))
        (folder/'arrangement.json').write_text(json.dumps({k:v for k,v in song.items() if k!='svg_pages'},ensure_ascii=False,indent=2)+'\n')
        songs.append(song)
        print(f"{song['id']}: {song['key']} {song['page_count']}p; {song['bass_pulses']} bass pulses; {song['bass_pulses_omitted']} rests",flush=True)
    protected=json.loads((ROOT/'songs/licensed/catalog.json').read_text()) if (ROOT/'songs/licensed/catalog.json').exists() else []
    collection={'songs':songs,'protected':protected,'instrument':LAYOUT}
    template=ROOT/'tools/practice_template.html'
    if template.exists():
        data=json.dumps(collection,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
        (ROOT/'practice/index.html').write_text(template.read_text().replace('__COLLECTION_JSON__',data))
    (ROOT/'docs/build-report.json').write_text(json.dumps({'generated':'2026-11-22','songs':len(songs),
        'melody_notes':sum(e['midi'] is not None for s in songs for m in s['measures'] for e in m['events']),
        'melody_rests':sum(e['midi'] is None for s in songs for m in s['measures'] for e in m['events']),
        'score_pages':sum(s['page_count'] for s in songs),'engraver':verovio.toolkit().getVersion(),'warnings':warnings},indent=2)+'\n')
    (ROOT/'downloads').mkdir(exist_ok=True)
    combined=fitz.open()
    combined.insert_pdf(songbook_cover(songs))
    guide=ROOT/'instrument/notes-pull-push.pdf'
    if guide.exists():combined.insert_pdf(fitz.open(guide))
    toc=[[1,'Lernfolge und Inhaltsverzeichnis',1]]
    if guide.exists():toc.append([1,'Instrument · Zug und Druck',2])
    for s in songs:
        toc.append([1,f"{s['learning_order']:02d} {s['title']}",len(combined)+1])
        combined.insert_pdf(fitz.open(ROOT/f"songs/public-domain/{s['id']}/score.pdf"))
    combined.set_toc(toc)
    combined.set_metadata({'title':'Christmas carols — Hohner Corso C/F learning songbook','author':'Akkordeon learning collection'})
    combined.save(ROOT/'downloads/christmas-corso-cf-songbook.pdf',garbage=4,deflate=True)
    return songs

if __name__=='__main__':main()
