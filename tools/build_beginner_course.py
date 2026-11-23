#!/usr/bin/env python3
"""Five progressive Corso C/F workbooks, using concert-pitch C major throughout."""
from __future__ import annotations
import copy
import io
import json
import math
import re
import subprocess
import wave
from pathlib import Path
from xml.etree import ElementTree as ET

import cairosvg
import fitz
import mido
import numpy as np
import verovio
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics

import build_collection as old

ROOT=Path(__file__).resolve().parents[1]
COURSE=ROOT/'course'
TMP=ROOT.parent/'tmp/pdfs/beginner_course'
TMP.mkdir(parents=True,exist_ok=True)
for d in ['data','audio','materials']:(COURSE/d).mkdir(parents=True,exist_ok=True)
LAYOUT=json.loads((ROOT/'instrument/corso-cf.json').read_text())
MEI=old.MEI;XMLID=old.XMLID
TEMPO=48;CAP=6
IDS=['morgen-kommt-der-weihnachtsmann','lasst-uns-froh','schneefloeckchen','alle-jahre-wieder','o-du-froehliche']
FILENAMES=['01_Morgen_kommt_der_Weihnachtsmann.pdf','02_Lasst_uns_froh_und_munter_sein.pdf',
           '03_Schneefloeckchen_Weissroeckchen.pdf','04_Alle_Jahre_wieder.pdf','05_O_du_froehliche.pdf']
INK='#173441';TEAL='#087E82';MUTED='#526571';ZCOLOR='#9E521F';PALE='#EDF5F4';LINE='#D1DFE1'
OPTIONS={}
for b in LAYOUT['melody']:
    for d in ['D','Z']:OPTIONS.setdefault(old.midi(b[d]),[]).append({'button':b['button'],'row':b['row'],'position':b['position'],'dir':d})

def normalise_source(source):
    s={'id':source['id'],'title':source['title'],'key':'C','meter':source['meter'],
       'composer':source.get('composer','Traditional'),'pickup_quarters':source.get('pickup_quarters',0),
       'practice_tempo_bpm':TEMPO,'tempo_bpm':TEMPO,'measures':[]}
    assert source['key']=='C', 'Course sources are written in concert-pitch C'
    start=0.;previous='C'
    for mi,sm in enumerate(source['measures']):
        m={'number':mi if s['pickup_quarters'] else mi+1,'start':start,'events':[],'bass':[]}
        offset=0.
        for ei,e in enumerate(sm['events']):
            p=e['pitch'];dur=float(e['duration'])
            m['events'].append({'id':f"{s['id']}-m{mi}-e{ei}",'pitch':p,'midi':None if p=='rest' else old.midi(p),
                               'duration':dur,'offset':offset,'start':start+offset,'tie':None})
            offset+=dur
        m['duration']=offset
        changes=copy.deepcopy(sm.get('chord_events') or [{'offset':0,'chord':sm.get('chord')}])
        if changes[0]['offset']!=0:changes.insert(0,{'offset':0,'chord':sm.get('chord')})
        m['harmony']=[]
        for ci,h in enumerate(changes):
            end=changes[ci+1]['offset'] if ci+1<len(changes) else offset
            orig=old.transpose_chord(h['chord'],0) if h['chord'] else None
            seg=[e for e in m['events'] if e['offset']<end and e['offset']+e['duration']>h['offset']]
            def cost(ch):
                return sum(e['duration']*(e['midi']%12 not in old.chord_pcs(ch)) for e in seg if e['midi'] is not None)+(0 if ch==previous else .05)
            chosen=orig if orig in ['C','F','G'] else min(['C','F','G'],key=cost)
            m['harmony'].append({'offset':float(h['offset']),'chord':chosen,'source_chord':h['chord']})
            previous=chosen
        s['measures'].append(m);start+=offset
    s['total_quarters']=start
    return s

def add_route(track,variant):
    s=copy.deepcopy(track);s['variant']=variant
    sounded=[]
    for m in s['measures']:
        for e in m['events']:
            if e['midi'] is None:e.update(button=None,row=None,position=None,dir=None)
            else:sounded.append((e,old.harmony_at(m,e['offset'])))
    if variant=='basic':
        for e,ch in sounded:
            choices=[o for o in OPTIONS[e['midi']] if o['row']=='C']
            assert len(choices)==1
            e.update(choices[0])
    else:
        # A state retains the accumulated sounding duration in the same direction.
        # Rests NEVER reset this bound; it is a routing constraint, not an air estimate.
        layers=[]
        for i,(e,ch) in enumerate(sounded):
            layer={};dur=round(e['duration']*2)
            for op in OPTIONS[e['midi']]:
                local=.30*(op['row']=='F')+.03*abs(op['position']-4)
                local+=.25*(old.available_left(ch,op['dir'],'bass') is None)
                if not i:
                    layer[(op['button'],op['dir'],dur)]=(local,None,op);continue
                for key,(cost,parent,prev) in layers[-1].items():
                    changed=op['dir']!=prev['dir'];run=dur if changed else key[2]+dur
                    if run>CAP*2:continue
                    c=cost+local+2.4*changed+1.0*(op['row']!=prev['row'])+.12*abs(op['position']-prev['position'])
                    state=(op['button'],op['dir'],run)
                    if state not in layer or c<layer[state][0]:layer[state]=(c,key,op)
            if not layer:raise ValueError(f"No bounded bonus route for {s['id']} note {i}")
            layers.append(layer)
        state=min(layers[-1],key=lambda k:layers[-1][k][0])
        for i in range(len(sounded)-1,-1,-1):
            cost,parent,op=layers[i][state];sounded[i][0].update(op);state=parent
        for mi,m in enumerate(s['measures']):
            if mi==0 and s['pickup_quarters']:continue
            pulses=[(0,'bass'),(1 if s['meter']=='3/4' else 2,'chord')]
            for off,kind in pulses:
                if off>=m['duration']:continue
                e=next((e for e in m['events'] if e['offset']<=off<e['offset']+e['duration']),None)
                if not e or e['midi'] is None:continue
                ch=old.harmony_at(m,off);d=e['dir'];button=old.available_left(ch,d,kind)
                if not button:continue
                end=min(off+.5,m['duration'])
                for later in m['events']:
                    if off<later['offset']<end and later['dir'] not in [d,None]:end=later['offset']
                for h in m['harmony']:
                    if off<h['offset']<end:end=h['offset']
                if end-off<.5-1e-8:continue
                ps=old.VOICINGS[ch] if kind=='chord' else [old.BASS_PITCHES[ch]]
                m['bass'].append({'start':m['start']+off,'offset':off,'duration':end-off,'button':button,'dir':d,
                                  'kind':kind,'chord':ch,'pitches':ps,'midis':[old.midi(p) for p in ps]})
    s['stats']=metrics(s)
    s['reset_windows']=[]
    for m in s['measures']:
        for e in m['events']:
            if e['midi'] is None and e['duration']>=.5 and abs(e['offset']+e['duration']-m['duration'])<1e-8:
                busy=any(b['start']<e['start']+e['duration'] and b['start']+b['duration']>e['start'] for b in m['bass'])
                if not busy:s['reset_windows'].append({'start':e['start'],'duration':e['duration'],'measure':m['number'],
                    'practice_stop_allowed':True,'automatic_air_reset':False})
    return s

def metrics(s):
    events=[e for m in s['measures'] for e in m['events'] if e['midi'] is not None]
    spans=[]
    for e in events:
        if not spans or e['dir']!=spans[-1]['dir']:
            spans.append({'dir':e['dir'],'start':e['start'],'end':e['start']+e['duration'],'sound':e['duration']})
        else:spans[-1]['end']=e['start']+e['duration'];spans[-1]['sound']+=e['duration']
    result={'reversals':len(spans)-1,'row_changes':sum(a['row']!=b['row'] for a,b in zip(events,events[1:])),
            'buttons':sorted({e['button'] for e in events}),'notes':len(events),'bass_pulses':sum(len(m['bass']) for m in s['measures']),
            'by_direction':{}}
    for d in ['D','Z']:
        selected=[r for r in spans if r['dir']==d]
        if not selected:
            result['by_direction'][d]={'sounding_quarters':0,'elapsed_quarters':0,'continuous_quarters':0,
                 'longest_sounding_seconds_at_48':0,'longest_span_start':None,'longest_span_end':None}
            continue
        longest=max(selected,key=lambda r:r['sound'])
        cont=0.;maxcont=0.;prev=None
        for e in events:
            if e['dir']==d:
                cont=cont+e['duration'] if prev and prev['dir']==d and abs(prev['start']+prev['duration']-e['start'])<1e-8 else e['duration']
                maxcont=max(maxcont,cont)
            else:cont=0.
            prev=e
        result['by_direction'][d]={'sounding_quarters':max(r['sound'] for r in selected),
             'elapsed_quarters':max(r['end']-r['start'] for r in selected),'continuous_quarters':maxcont,
             'longest_sounding_seconds_at_48':max(r['sound'] for r in selected)*60/TEMPO,
             'longest_span_start':longest['start'],'longest_span_end':longest['end']}
    return result

def ex_arrays(id):
    C='C4';D='D4';E='E4';F='F4';G='G4';A='A4';B='B4';H='C5';R='rest'
    return {
      IDS[0]:[
       ('Vier Schläge Druck, vier Schläge Zug','4/4',[[(C,1),(E,1),(G,1),(G,1)],[(D,1),(F,1),(A,1),(A,1)]],
        'Balg in mittlerer Öffnung beginnen. Gleichmäßig bewegen; die Tonknöpfe einzeln lösen.'),
       ('Ein Knopf - zwei Töne','4/4',[[(C,1),(D,1),(C,1),(D,1)],[(E,1),(F,1),(G,1),(A,1)]],
        'Vor jedem Richtungswechsel den Ton kurz beenden. Kein zusätzlicher Ruck auf jedem Ton.'),
       ('Dein Liedanfang','4/4',[[(C,1),(C,1),(G,1),(G,1)],[(A,1),(A,1),(G,2)]],
        'Gleiche Töne durch erneutes Drücken des Knopfs trennen. Die Halbe zwei Schläge halten.')],
      IDS[1]:[
       ('Achtel mit ruhigem Balg','4/4',[[(G,.5)]*8,[(F,.5)]*6+[(R,1)]],
        'Laut 1-und-2-und-3-und-4-und zählen. Jede Achtel ist ein halber Viertelschlag.'),
       ('G-A-G-F: Wechsel in kleinen Schritten','4/4',[[(G,.5),(A,.5),(G,.5),(F,.5),(E,1),(R,1)],[(F,.5),(G,.5),(F,.5),(E,.5),(D,1),(R,1)]],
        'Erst bei 40 Vierteln pro Minute. Auf den Achteln den Balg kontrolliert wechseln.'),
       ('Der neue hohe Ton C5','4/4',[[(G,1),(H,1),(G,1),(R,1)],[(H,1),(G,1),(E,1),(C,1)]],
        'C5 sitzt auf Knopf 6 auf Druck. Der Ton liegt höher als das mittlere C4 auf Knopf 3.')],
      IDS[2]:[
       ('Dreierpuls','3/4',[[(C,1),(E,1),(G,1)],[(D,1),(F,1),(A,1)]],
        '1-2-3 zählen. Die 1 leicht betonen; die nächsten beiden Schläge bleiben ruhig.'),
       ('Auftakt auf 3-und','3/4',[[(R,2),(E,.5),(F,.5)],[(G,1),(G,1),(A,1)]],
        '1 und 2 nur zählen. E kommt auf 3, F auf und; der nächste volle Takt beginnt mit G.'),
       ('C5-B4-A4 lesen','3/4',[[(H,1),(B,1),(A,1)],[(G,.5),(F,.5),(E,1),(D,1)]],
        'B4 heißt im deutschen System H. Auf Knopf 6: C5 auf Druck, B4 auf Zug.')],
      IDS[3]:[
       ('Punktierte Viertel und Achtel','4/4',[[(G,1.5),(A,.5),(G,1),(F,1)],[(E,2),(D,2)]],
        'G über 1-und-2 halten, A auf 2-und spielen. Dann G auf 3 und F auf 4.'),
       ('Kurze Achtelpaare im Puls','4/4',[[(C,1),(D,.5),(E,.5),(F,1),(E,1)],[(G,1),(F,.5),(E,.5),(F,1),(G,1)]],
        'Die beiden Achtel teilen genau einen Schlag. Die umliegenden Viertel nicht beschleunigen.'),
       ('Obere Lage und langer Schluss','4/4',[[(H,2),(B,1),(A,1)],[(E,3),(R,1)]],
        'C5 zwei Schläge halten. Am Schluss E drei Schläge halten, dann einen Schlag pausieren.')],
      IDS[4]:[
       ('Halbe und punktierte Viertel verbinden','4/4',[[(G,2),(A,2)],[(G,1.5),(F,.5),(E,1),(F,1)]],
        'Den Balg bei langen Tönen gleichmäßig bewegen. Leise spielen spart Kraft und meist Luft.'),
       ('Der Bogen zum oberen C','4/4',[[(A,2),(B,1),(H,1)],[(B,2),(A,2)]],
        'A und B liegen auf Zug, C5 auf Druck. Die beiden langen Zugtöne am Ende benötigen vier Schläge.'),
       ('Den Schluss sicher landen','4/4',[[(E,2),(D,2)],[(C,3),(R,1)]],
        'Bis zum letzten C mitzählen. Die Schlussnote nicht verkürzen; erst in der Pause entspannen.')]
    }[id]

def make_exercises(id):
    result=[]
    for i,(title,meter,arrays,tip) in enumerate(ex_arrays(id)):
        src={'id':id+f'-exercise-{i+1}','title':title,'key':'C','meter':meter,'pickup_quarters':0,
             'measures':[{'events':[{'pitch':p,'duration':d} for p,d in a],'chord':'C'} for a in arrays]}
        s=add_route(normalise_source(src),'basic');s['tip']=tip;result.append(s)
    return result

def single_note_quiz(id):
    pitches=['F4','C4','A4','D4','G4','E4'] if id==IDS[0] else (['C5','D4','F4','G4','E4','C4','A4','G4'] if id==IDS[1] else ['F4','C5','D4','B4','G4','E4','A4','C4'])
    pitches=pitches+['rest']*((-len(pitches))%4)
    src={'id':id+'-quiz','title':'Noten lesen','key':'C','meter':'4/4','pickup_quarters':0,
         'measures':[{'events':[{'pitch':p,'duration':1} for p in pitches[i:i+4]],'chord':'C'} for i in range(0,len(pitches),4)]}
    s=add_route(normalise_source(src),'basic');s['answers']=[p for p in pitches if p!='rest'];return s

def musicxml(s,bass=False,labels=True):
    root=ET.fromstring(old.musicxml(s))
    for measure in root.findall('./part/measure'):
        if not bass:
            for el in list(measure):
                if el.tag in ['backup','harmony'] or (el.tag=='note' and el.findtext('staff')=='2'):measure.remove(el)
            att=measure.find('attributes')
            if att is not None:
                att.find('staves').text='1'
                for cl in list(att.findall('clef')):
                    if cl.attrib.get('number')=='2':att.remove(cl)
        if not labels:
            for n in measure.findall('note'):
                for lyric in list(n.findall('lyric')):n.remove(lyric)
    ET.indent(root,space='  ')
    return '<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(root,encoding='unicode')+'\n'

def score_asset(s,label_mode='guided',bass=False,per_line=4):
    sub=old.sub
    root=ET.Element('mei',xmlns=MEI,meiversion='5.0');head=sub(root,'meiHead');fd=sub(head,'fileDesc');sub(sub(fd,'titleStmt'),'title',s['title']);sub(fd,'pubStmt')
    score=sub(sub(sub(sub(root,'music'),'body'),'mdiv'),'score');n,d=s['meter'].split('/')
    sd=sub(score,'scoreDef',**{'meter.count':n,'meter.unit':d,'key.sig':'0'})
    sg=sub(sd,'staffGrp',**({'symbol':'brace','bar.thru':'false'} if bass else {}))
    sub(sg,'staffDef',n=1,lines=5,**{'clef.shape':'G','clef.line':'2'})
    if bass:sub(sg,'staffDef',n=2,lines=5,**{'clef.shape':'F','clef.line':'4'})
    section=sub(score,'section')
    for mi,m in enumerate(s['measures']):
        if mi and ((not s['pickup_quarters'] and mi%per_line==0) or (s['pickup_quarters'] and (mi-1)%per_line==0 and mi>1)):sub(section,'sb')
        attrs={'n':m['number'],XMLID:s['id']+f'-measure-{mi}'}
        if m['duration']!=int(n)*4/int(d):attrs['metcon']='false'
        if mi==len(s['measures'])-1:attrs['right']='end'
        me=sub(section,'measure',**attrs);ties=[]
        for staff,events in [(1,m['events'])]+([(2,old.bass_sequence(m))] if bass else []):
            lay=sub(sub(me,'staff',n=staff),'layer',n=1);beams=[];offset=0.
            for ei,e in enumerate(events):
                ps=([e['pitch']] if e.get('pitch')!='rest' else []) if staff==1 else e['pitches']
                prev=[]
                for ci,duration in enumerate(old.splits(e['duration'])):
                    typ,dots=old.BEATS[duration];a={'dur':old.DUR[typ]}
                    if dots:a['dots']=dots
                    parent=lay if len(ps)<=1 else sub(lay,'chord',**a)
                    nodes=[]
                    for ni,p in enumerate(ps or [None]):
                        if not p:sub(lay,'rest',**a);continue
                        letter,acc,octave=old.note_parts(p);nid=f"{s['id']}-s{staff}-m{mi}-e{ei}-c{ci}-n{ni}"
                        na={'pname':letter.lower(),'oct':octave,XMLID:nid}
                        if len(ps)<=1:na.update(a)
                        if acc:na['accid']='s' if acc=='#' else 'f'
                        node=sub(parent,'note',**na);nodes.append(node)
                        if ni<len(prev):ties.append((prev[ni],nid))
                    if nodes and ci==0 and label_mode=='guided':
                        v=sub(nodes[0],'verse',n=1);sub(v,'syl',f"{e['button']}{e['dir']}")
                        v=sub(nodes[0],'verse',n=2);sub(v,'syl',e['pitch'] if staff==1 else e['chord'])
                    prev=[node.attrib[XMLID] for node in nodes]
            # Regular eighth-note beams, grouped within each quarter beat.
            children=list(lay);groups=[];group=[];last=None;offset=0.
            for child in children:
                dur=4/int(child.attrib['dur'])*(1.5 if child.attrib.get('dots') else 1)
                beat=int(offset+1e-8);short=child.tag in ['note','chord'] and dur<1
                if not short or beat!=last:
                    if group:groups.append(group)
                    group=[]
                if short:group.append(child)
                else:groups.append([child])
                offset+=dur;last=beat
            if group:groups.append(group)
            for child in children:lay.remove(child)
            for group in groups:
                target=sub(lay,'beam') if len(group)>1 else lay
                for child in group:target.append(child)
        for a,b in ties:sub(me,'tie',startid='#'+a,endid='#'+b)
        if bass:
            for h in m['harmony']:sub(me,'harm',h['chord'],staff=1,tstamp=1+h['offset'],place='above')
    mei=ET.tostring(root,encoding='unicode')
    tk=verovio.toolkit();tk.setOptions({'inputFrom':'mei','font':'Bravura','fontTextLiberation':False,
        'smuflTextFont':'none','pageWidth':1950 if bass else 1700,'pageHeight':8000,'pageMarginTop':30,
        'pageMarginBottom':35,'pageMarginLeft':30,'pageMarginRight':30,'scale':40,'breaks':'encoded',
        'adjustPageHeight':True,'header':'none','footer':'none','lyricSize':4.7 if not bass else 4.3,
        'lyricWordSpace':1.7,'spacingStaff':14,'spacingSystem':7,'minLastJustification':0,
        'xmlIdChecksum':True,'svgViewBox':True})
    if not tk.loadData(mei) or tk.getPageCount()!=1:raise ValueError('Engraving failed '+s['id'])
    svg=tk.renderToSVG(1);pdf=cairosvg.svg2pdf(bytestring=svg.encode())
    asset=fitz.open(stream=pdf,filetype='pdf')
    name=s['id']+'-'+label_mode+('-bass' if bass else '')
    (TMP/(name+'.svg')).write_text(svg)
    return {'pdf':pdf,'width':asset[0].rect.width,'height':asset[0].rect.height,'id':name}

def midi_file(track,path):
    mf=mido.MidiFile(ticks_per_beat=480)
    for no in [0,1]:
        tr=mido.MidiTrack();mf.tracks.append(tr);tr.append(mido.MetaMessage('track_name',name='Melody' if no==0 else 'Optional bass'))
        tr.append(mido.Message('program_change',program=21,channel=no))
        if no==0:tr.append(mido.MetaMessage('set_tempo',tempo=mido.bpm2tempo(TEMPO)))
        ev=[]
        for m in track['measures']:
            for e in m['events'] if no==0 else m['bass']:
                ns=([e['midi']] if e['midi'] is not None else []) if no==0 else e['midis']
                for pitch in ns:ev.extend([(round(e['start']*480),1,pitch),(round((e['start']+e['duration']*.92)*480),0,pitch)])
        last=0
        for time,on,p in sorted(ev):
            tr.append(mido.Message('note_on' if on else 'note_off',note=p,velocity=(70 if no==0 else 46) if on else 0,channel=no,time=time-last));last=time
        tr.append(mido.MetaMessage('end_of_track',time=round(track['total_quarters']*480)-last))
    mf.save(path)

def audio_file(s,path):
    # Original synthesized practice tone; no recording or commercial sample used.
    sr=22050;beat=60/TEMPO;bar=int(s['meter'].split('/')[0]);pre=bar-s['pickup_quarters'] if s['pickup_quarters'] else bar
    length=(pre+s['total_quarters']+1)*beat;audio=np.zeros(math.ceil(length*sr))
    def add(start,duration,freq,gain,click=False):
        n=max(1,int(duration*sr));t=np.arange(n)/sr
        tone=np.sin(2*np.pi*freq*t)
        if click:env=np.exp(-t*75)
        else:
            tone=(tone+.30*np.sin(4*np.pi*freq*t)+.10*np.sin(6*np.pi*freq*t))/1.4
            env=np.ones(n);a=min(n//3,int(.02*sr));r=min(n//3,int(.055*sr));env[:a]=np.linspace(0,1,a);env[-r:]=np.linspace(1,0,r)
        pos=int(start*sr);audio[pos:pos+n]+=gain*tone*env
    for b in range(math.ceil(pre+s['total_quarters'])):add(b*beat,.045,1700 if b%bar==0 else 1200,.10,True)
    for m in s['measures']:
        for e in m['events']:
            if e['midi'] is not None:add((pre+e['start'])*beat,e['duration']*beat*.92,440*2**((e['midi']-69)/12),.55)
    wav=TMP/(s['id']+'.wav')
    with wave.open(str(wav),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(audio,-.98,.98)*32767).astype('<i2').tobytes())
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(wav),'-codec:a','libmp3lame','-q:a','4',str(path)],check=True)
    return {'tempo_bpm':TEMPO,'tempo_unit':'quarter','count_in_quarters':pre,'duration_seconds':length,
            'synthetic':True,'metronome':True,'melody_only':True}

def wrap(text,font,size,width):
    lines=[]
    for para in text.split('\n'):
        line=''
        for word in para.split():
            test=line+' '+word if line else word
            if line and pdfmetrics.stringWidth(test,font,size)>width:lines.append(line);line=word
            else:line=test
        lines.append(line)
    return lines

class Book:
    def __init__(self,title):
        self.buf=io.BytesIO();self.c=canvas.Canvas(self.buf,pagesize=A4);self.c.setTitle(title);self.c.setAuthor('Akkordeon C/F Lernkurs')
        self.w,self.h=A4;self.slots=[];self.page=0
    def text(self,x,y,text,size=10,bold=False,color=INK):
        self.c.setFillColor(HexColor(color));self.c.setFont('Bold' if bold else 'Body',size);self.c.drawString(x,self.h-y,text)
    def para(self,y,text,size=10,width=507,x=44,color=INK,bold=False,leading=None):
        lead=leading or size*1.35
        for line in wrap(text,'Bold' if bold else 'Body',size,width):self.text(x,y,line,size,bold,color);y+=lead
        return y
    def header(self,title,stage,subtitle):
        self.page+=1;self.text(44,31,'HOHNER CORSO C/F  |  ANFÄNGERKURS BIS 24.12.2026',8,True,TEAL)
        size=20
        while pdfmetrics.stringWidth(title,'Bold',size)>507:size-=.5
        self.text(44,59,title,size,True);self.text(44,79,stage,10,True)
        self.text(44,96,subtitle,8.7,color=MUTED);self.c.setStrokeColor(HexColor(TEAL));self.c.setLineWidth(.8);self.c.line(44,self.h-106,551,self.h-106)
    def section(self,y,title):self.text(44,y,title,12,True,TEAL);return y+19
    def box(self,y,height,title,body):
        self.c.setFillColor(HexColor(PALE));self.c.roundRect(44,self.h-y-height,507,height,8,fill=1,stroke=0)
        self.text(56,y+18,title,10,True);self.para(y+35,body,9.3,483,56)
    def score(self,asset,top,maxheight,width=507):
        natural=asset['height']*width/asset['width'];height=min(maxheight,natural);actual_w=width*height/natural
        x=44+(width-actual_w)/2
        self.slots.append((self.page-1,fitz.Rect(x,top,x+actual_w,top+height),asset['pdf']))
        return top+height
    def check(self,x,y,label,size=9):
        self.c.setStrokeColor(HexColor(MUTED));self.c.setLineWidth(.7);self.c.rect(x,self.h-y-1,8,8);self.text(x+14,y+6,label,size)
    def footer(self,left='Zahlen = Knöpfe, keine Finger. D = Druck; Z = Zug. B = deutsches H.'):
        self.text(44,803,left,7.3,color=MUTED);self.text(44,818,'Eigener Lernkurs · Quellen und Belegung: github.com/hailbagel/Akkordeon',7,color=MUTED)
        self.c.drawRightString(551,self.h-818,str(self.page));self.c.showPage()
    def save(self,path):
        self.c.save();doc=fitz.open(stream=self.buf.getvalue(),filetype='pdf')
        for page,rect,data in self.slots:doc[page].show_pdf_page(rect,fitz.open(stream=data,filetype='pdf'),0)
        doc.save(path,garbage=4,deflate=True)

def fmt(v):return f'{v:g}'.replace('.',',')

def workbook(song,content):
    base=song['variants']['basic'];bonus=song['variants']['bonus'];title=song['title'];idx=song['index']
    guided=score_asset(base);reader=score_asset(base,'reader');extra=score_asset(bonus,bass=True)
    exercises=[score_asset(e,per_line=2) for e in song['exercises']];quiz=score_asset(song['reading_quiz'],'reader',per_line=2)
    b=Book(title+' - vier Lernstufen auf der Hohner Corso C/F')
    b.header(title,'1 / 4  ·  Vorübungen: verstehen und ausprobieren',f"Lied {idx} von 5  ·  {content['date_range']}  ·  Übungstempo: Viertel = 40, später 48")
    b.box(119,65,'Dein Lernziel',content['goal']+' '+content['new_skill'])
    needed='3–5' if idx==1 else '3–6'
    b.para(204,f'Grundlage: äußere C-Reihe. Knöpfe 1–11 vom Kinn zum Knie zählen. Du brauchst hier die Knöpfe {needed}; siehe Knopfkarte im Lehrplan. Der gleiche Knopf liefert auf Zug und Druck verschiedene Töne.',9.5)
    y=257
    for i,(ex,asset) in enumerate(zip(song['exercises'],exercises),1):
        b.text(44,y,f"Übung {i}: {ex['title']}",10.5,True);y+=18
        y=b.para(y,ex['tip'],9.2,leading=12.3)+5
        y=b.score(asset,y,100)+24
    b.box(710,62,'Weiter zu Seite 2, wenn …','jede Übung dreimal ruhig gelingt. Bei Unsicherheit nur einen Takt wiederholen. Erst die Töne benennen, dann den Knopf und die Balgrichtung nennen, dann spielen.')
    b.footer()

    b.header(title,'2 / 4  ·  Das ganze Lied mit Lernhilfen',f"C-Dur  ·  {base['meter']}  ·  Viertel = 48  ·  rechte Hand allein  ·  nur C-Reihe")
    b.para(123,'Oben die Noten, darunter Knopf + Richtung und Tonname. C4 ist das mittlere C. Gleiche Töne durch Lösen des Knopfs trennen; den Balg gleichmäßig weiterbewegen.',9.4)
    end=b.score(guided,158,430)
    y=max(438,end+22)
    b.text(44,y,'In diesen Abschnitten üben',11,True,TEAL);y+=18
    y=b.para(y,'  |  '.join(content['chunks']),9.5)+8
    for step in content['practice_steps'][:3]:y=b.para(y,'• '+step,9.2,leading=12.5)+4
    st=base['stats'];d=st['by_direction']['D'];z=st['by_direction']['Z']
    b.box(668,101,'Dein Balgweg in der Basisfassung',
      f"{st['reversals']} Richtungswechsel; keine Reihenwechsel. Längste Folge: klingend D {fmt(d['sounding_quarters'])} / Z {fmt(z['sounding_quarters'])} Viertel; "
      f"mit Pausen D {fmt(d['elapsed_quarters'])} / Z {fmt(z['elapsed_quarters'])}. "
      'Eine Pause setzt die Richtungsfolge nicht zurück. Die Zahlen messen Dauer, nicht Luftverbrauch. Vor Druck Platz zum Zusammendrücken lassen, vor Zug Platz zum Öffnen. Beim Üben nach einem Abschnitt ohne Ton mit der Lufttaste neu einrichten.')
    b.footer()

    b.header(title,'3 / 4  ·  Selbstständig Noten lesen und Fortschritt festhalten',f"Gleiche Melodie, gleiche äußere C-Reihe. Die Knopfzahlen und Tonnamen sind jetzt ausgeblendet.")
    b.para(124,'Decke Seite 2 ab. Lies zuerst Tonhöhe und Notenwert, entscheide dann über Knopf und Richtung. Wenn du nur auswendig spielst, nutze zusätzlich die Leseprobe unten.',9.2)
    end=b.score(reader,153,285);y=max(373,end+16)
    b.text(44,y,'Leseprobe: Tonname + Griff nennen; Lösung unten abdecken',10,True,TEAL)
    y=b.score(quiz,y+14,65)+10
    y=540;b.text(44,y,'Mein Fortschritt',11,True,TEAL);y+=21
    rows=[('Alle Noten der Leseprobe erkannt','ohne Hilfen'),('Vorübungen sicher','Viertel = 40'),
          ('Ganzes Lied mit Hilfen','Viertel = 48'),('Ganzes Lied ohne Knopfzahlen','Viertel = 48'),
          ('Ruhiger Vorspieldurchgang',f"Viertel = {content['target_bpm']}"),('Bonus mit beiden Händen','freiwillig')]
    b.text(44,y,'Meilenstein',8.6,True);b.text(340,y,'3 gute Durchgänge',8.6,True);b.text(466,y,'Datum / Tempo',8.6,True);y+=21
    for label,tempo in rows:
        b.text(44,y,label,8.7);b.text(44,y+12,tempo,7.5,color=MUTED)
        for x in [350,378,406]:b.check(x,y-5,'')
        b.text(466,y,'____________',8,color=MUTED);b.text(466,y+12,'_____ BPM',7.5,color=MUTED);y+=29
    b.para(754,'Ein guter Durchgang: ohne Neustart, im ruhigen Puls und mit höchstens einem Tonfehler. Das Tempo darf niedriger bleiben. Bässe sind kein Pflichtkriterium für Weihnachten.',8.2)
    b.footer('Lösung Leseprobe: '+', '.join(song['reading_quiz']['answers'])+'  ·  B4 = H4.')

    b.header(title,'4 / 4  ·  Bonus: beide Reihen und einfache Bassimpulse',
             'Erst beginnen, wenn die Basis sicher ist. Die Melodie bleibt unverändert; die Griffroute wechselt.')
    bs=bonus['stats'];b.para(123,
      f"Alternative: {bs['reversals']} Balgwechsel statt {st['reversals']}; dafür {bs['row_changes']} Reihenwechsel. "
      f"Längste Folge: klingend D {fmt(bs['by_direction']['D']['sounding_quarters'])} / Z {fmt(bs['by_direction']['Z']['sounding_quarters'])} Viertel; "
      f"mit Pausen D {fmt(bs['by_direction']['D']['elapsed_quarters'])} / Z {fmt(bs['by_direction']['Z']['elapsed_quarters'])}. "
      'Weniger Wechsel kann mehr Balgweg und andere Knöpfe erfordern.',9)
    end=b.score(extra,174,442)
    y=max(637,end+16);b.text(44,y,'So erweiterst du das Lied',10.5,True,TEAL);y+=17
    y=b.para(y,content['bonus_tip'],9,leading=12)+6
    b.para(y,'1. Neue Melodiegriffe allein üben. 2. Nur Grundbässe L2/L4 ergänzen. 3. Erst dann die Akkordknöpfe L1/L3. Links stehen kurze Achtelimpulse mit Pausen; beides genau einhalten.',9,leading=12)
    b.para(738,'Bassschema: L2 = C auf Druck / G auf Zug; L1 = dazugehöriger Durakkord. L4 = F auf Druck / C auf Zug; L3 = dazugehöriger Durakkord. Knopflage: Lehrplan, Seite 4.',8.4)
    b.footer('Bassnoten sind schematische Leselagen. Pausen sind gewollt; Bass vor einem Balgwechsel lösen.')
    out=COURSE/FILENAMES[idx-1];b.save(out);return str(out.relative_to(ROOT))

def plan_pdf(songs):
    b=Book('Dein Weihnachtskurs - fünf Melodien auf der Hohner Corso C/F')
    b.header('Fünf Weihnachtslieder bis Heiligabend','Dein Lehrplan · 23. November bis 24. Dezember 2026','Annahme: etwa 20 Minuten an sechs Tagen pro Woche; ein leichter Wiederholungs- oder Ruhetag.')
    b.box(120,71,'Das erreichbare Ziel','Fünf ganze Melodien langsam und zusammenhängend spielen. Du lernst die Noten dazu und findest ihre Knöpfe auf deiner Corso C/F. Beide Hände und schnellere Tempi sind Erweiterungen, keine Pflicht bis Weihnachten.')
    y=b.section(214,'Dein Fahrplan')
    for s in songs:
        c=s['lesson'];b.text(44,y,f"{s['index']:02d}  {s['title']}",11,True);b.text(365,y,c['date_range'],9,True,TEAL);y+=17
        y=b.para(y,c['new_skill'],9.5,leading=13)+16
    y=b.para(y,'23.12.: alle fünf Melodien mit kurzen Pausen als Generalprobe. 24.12.: ruhiges Vorspiel. Bei Zeitdruck lieber die fünf sicheren Basisfassungen als eine unsichere Begleitung.',9.3)+14
    y=b.section(y,'Eine Übeeinheit: 20 Minuten')
    for line in ['3 Minuten: Noten erkennen, Hände locker, Balg mit Lufttaste in eine mittlere Öffnung setzen.',
                 '5 Minuten: eine Vorübung oder schwierige Zweitaktstelle, zunächst bei Viertel = 40–48.',
                 '8 Minuten: das aktuelle Lied abschnittweise verbinden; mindestens ein älteres Lied kurz wiederholen.',
                 '4 Minuten: ein ruhiger Durchgang ohne Neustart; Datum und Fortschritt im Liedheft eintragen.']:
        y=b.para(y,'• '+line,9.3,leading=12.5)+5
    b.box(710,65,'Wenn eine Stelle nicht klappt','Tempo senken, auf einen Takt verkürzen und die Richtung vorher laut nennen. Erst nach drei ruhigen Wiederholungen erweitern. Bei nur zehn Minuten am Tag entfallen zuerst die Bonusbässe; den Notenleseteil beibehalten.')
    b.footer('Der Zeitplan ist ein Übevorschlag, keine Erfolgsgarantie. Dein tatsächlicher Fortschritt bestimmt das Tempo.')

    b.header('Dein Instrument und deine Noten','Grundlagen · damit du nicht nur Knopfzahlen nachspielst','Hohner Corso C/F · Wiener Zweireiher · 21 Melodieknöpfe und 8 Bassknöpfe · kein Club-System')
    y=b.section(128,'Die vier Knöpfe für alle fünf Basisfassungen')
    b.para(y,'Äußere C-Reihe: vom Kinn zum Knie zählen, einschließlich der beiden ersten Knöpfe. In diesem Kurs spielst du zunächst nur 3, 4, 5 und 6.',9.5);y+=43
    for i,number in enumerate([3,4,5,6]):
        x=44+i*130;b.c.setFillColor(HexColor(PALE));b.c.roundRect(x,b.h-y-84,117,84,7,fill=1,stroke=0)
        entry=next(e for e in LAYOUT['melody'] if e['button']==number)
        b.text(x+12,y+20,f'Knopf {number}',11,True);b.text(x+12,y+42,'Druck: '+entry['D'],10,True);b.text(x+12,y+63,'Zug: '+entry['Z'],10,True,ZCOLOR)
    y+=105;b.para(y,'Mögliche Handlage: Zeigefinger auf 3, Mittelfinger auf 4, Ringfinger auf 5, kleiner Finger auf 6. Die Nummern unter den Noten bezeichnen Knöpfe, keine Finger. Nichts mit Kraft erzwingen.',9.3);y+=55
    b.text(44,y,'Notenpass C4 bis C5',11,True,TEAL);y+=13
    src={'id':'course-note-pass','title':'Notenpass','key':'C','meter':'4/4','pickup_quarters':0,
         'measures':[{'events':[{'pitch':p,'duration':1} for p in ['C4','D4','E4','F4']]},
                     {'events':[{'pitch':p,'duration':1} for p in ['G4','A4','B4','C5']]}]}
    asset=score_asset(add_route(normalise_source(src),'basic'),per_line=2);y=b.score(asset,y,110)+14
    y=b.para(y,'C4: kurze Hilfslinie unter dem System. E4: unterste Linie. G4: zweite Linie von unten. B4: mittlere Linie; international B, deutsch H. C5: Raum direkt darüber. Linien und Zwischenräume wechseln sich ab.',9.2)+12
    y=b.section(y,'Dauer lesen und zählen')
    rhythm={'id':'course-rhythm-pass','title':'Rhythmuspass','key':'C','meter':'4/4','pickup_quarters':0,
       'measures':[{'events':[{'pitch':p,'duration':d} for p,d in a]} for a in [
           [('E4',2),('E4',1),('E4',.5),('E4',.5)],
           [('E4',1.5),('E4',.5),('rest',1),('E4',1)],
           [('E4',3),('rest',1)]]]}
    y=b.score(score_asset(normalise_source(rhythm),'reader',per_line=3),y-4,80)+10
    rows=[('Viertel','1 Schlag'),('Halbe','2 Schläge'),('Achtel','ein halber Schlag: Zahl oder und'),
          ('Punktierte Viertel','1½ Schläge: drei Achtel'),('Punktierte Halbe','3 Schläge')]
    for label,desc in rows:b.text(44,y,label,9.5,True);b.text(220,y,desc,9.5);y+=19
    b.para(y+3,'4/4: 1-und-2-und-3-und-4-und. 3/4: 1-und-2-und-3-und. Bei Schneeflöckchen kommen die beiden Auftakt-Achtel auf 3-und. Ein Halbton beschreibt einen Tonhöhenabstand, keine Notendauer.',9.3)
    b.footer()

    b.header('Balgkontrolle und Fortschritt','Druck und Zug bewusst planen','Das Ziel ist ein ruhiger Ton mit genügend Bewegungsreserve, nicht die längste Strecke in einer Richtung.')
    y=b.section(129,'Drei Regeln, die du wirklich brauchst')
    for title,body in [
      ('1. Erst eine Reihe','Die Basisfassungen bleiben auf der C-Reihe. So sind die Knöpfe leicht zu merken. Dafür wechseln Zug und Druck häufiger. Bei gleichem Knopf kann ein Richtungswechsel den Ton verändern.'),
      ('2. Lange Richtung braucht Platz','Vor einer längeren Druckfolge den Balg passend öffnen, vor Zug genug Öffnungsweg lassen. Nicht bis an die Anschläge spielen. Die Anzahl der Viertelschläge misst Notendauer, nicht Luftverbrauch.'),
      ('3. Lufttaste gezielt benutzen','Beim Einrichten und zwischen Übeabschnitten: Ton- und Bassknöpfe loslassen, Lufttaste öffnen, Balg in eine bequeme Position bringen. Im laufenden Lied nur in geeigneten Pausen nachsetzen. Beim Üben darfst du dafür stoppen.')]:
        b.text(44,y,title,10.5,True);y+=17;y=b.para(y,body,9.4,leading=13)+16
    y=b.section(y,'Basis und Bonus haben verschiedene Aufgaben')
    y=b.para(y,'In der Bonusfassung werden einige Töne auf der inneren F-Reihe gegriffen. Beispielsweise ist G4 sowohl 5D als auch 14Z. Dadurch lässt sich eine Balgwendung vermeiden; gleichzeitig entstehen längere Richtungsspannen. Im Kurs ist jede Bonusroute auf höchstens sechs klingende Viertelschläge in gleicher Richtung begrenzt. Pausen setzen diese Rechnung nicht zurück.',9.3)+10
    y=b.para(y,'Bei Viertel = 48 dauern sechs Viertel bereits 7,5 Sekunden. Ob dein Balg dafür ausreicht, hängt unter anderem von Lautstärke, Stimmchören und Bassspiel ab. Die Grenze ist eine Lernvorgabe und keine Zusicherung über den Luftvorrat.',9.3)+19
    b.box(y-3,77,'Die drei neuen Knöpfe im Bonus',
          'Innere F-Reihe: 12–21, ebenfalls vom Kinn zum Knie. Knopf 13 ist der zweite, 14 der dritte und 15 der vierte Knopf dieser Reihe.\n13Z = E4  ·  14D = F4  ·  14Z = G4  ·  15D = A4')
    y+=93
    y=b.section(y,'Wann gilt ein Lied als gelernt?')
    for line in ['Du erkennst die Noten einer gemischten Leseprobe auch außerhalb der Liedreihenfolge.',
                 'Du spielst das ganze Lied dreimal ohne Neustart, mit ruhigem Puls und höchstens einem Tonfehler.',
                 'Du findest rechtzeitig eine bequeme Balgposition und kannst die heikle Stelle benennen.',
                 'Du spielst am nächsten Tag noch einmal sicher. Das Abhaken kommt erst nach dem Spielen.']:
        b.check(44,y,line,9);y+=25
    b.para(730,'Quellen: konkrete C/F-Grifftabelle im Instrumentordner; Melodiequellen in course/data und der bisherigen Sammlung. Methodenabgleich: Henry Doktorski, Organetto und Mel Biggs. Eigene Übungen und Lernfassungen; keine moderne Verlagsschule kopiert.',8.3)
    b.footer('Druck = Balg schließen. Zug = Balg öffnen. Die Lufttaste ersetzt keine fehlende Melodienote.')

    b.header('Linke Hand: der freiwillige Bonus','Grundbässe und Akkorde zuordnen','Erst die Melodie sicher spielen. Auch ohne Bassbegleitung führst du mit der linken Hand den Balg.')
    b.para(125,'L1–L8 sind unsere Kennungen, keine genormten Herstellernummern. Das Schema folgt der Anordnung des Referenzblatts: zwei Viererreihen, die Balgseite darunter. Vor dem ersten Bonus die Knöpfe und Töne am eigenen Instrument zuordnen.',9.3)
    for entry in LAYOUT['bass']:
        x=44+(entry['diagram_column']-1)*130;y=179+(entry['diagram_row']-1)*100
        b.c.setFillColor(HexColor(PALE if entry['diagram_row']==1 else '#F4F5F5'))
        b.c.roundRect(x,b.h-y-86,117,86,7,fill=1,stroke=0)
        b.text(x+10,y+18,entry['button']+' · '+('Akkord' if entry['kind']=='chord' else 'Grundbass'),9.2,True)
        def label(ch):
            return ch[:-1]+'-Moll' if ch.endswith('m') else ch+('-Dur' if entry['kind']=='chord' else '')
        b.text(x+10,y+42,'D: '+label(entry['D']),9.6,True)
        b.text(x+10,y+64,'Z: '+label(entry['Z']),9.6,True,ZCOLOR)
    b.c.setStrokeColor(HexColor(LINE))
    for line in range(4):b.c.line(44,b.h-382-line*5,551,b.h-382-line*5)
    b.text(248,413,'BALGSEITE',8,True,MUTED)
    y=b.section(443,'Die ersten Bassversuche: ohne Melodie')
    for line in [
       'C-Dur auf Druck: L2 auf Zählzeit 1 kurz antippen, L1 auf 3 kurz antippen. Jeden Knopf nach einer Achtel lösen; die übrige Zeit pausieren.',
       'G-Dur auf Zug: dieselben beiden Knöpfe und denselben Rhythmus verwenden. Die andere Balgrichtung verändert Grundbass und Akkord.',
       'F-Dur auf Druck: genauso mit L4 als Grundbass und L3 als Akkord. Auf Zug liefern diese Knöpfe C-Dur.',
       'Im Lied nur die tatsächlich notierten Einsätze übernehmen. Im Dreiertakt liegt der optionale Akkord auf 2. Wo eine Pause steht, links nichts drücken.']:
        y=b.para(y,'• '+line,9.3,leading=13)+8
    b.box(643,88,'Ein Knopf kann mehrere Töne auslösen','L1 und L3 sind Akkordknöpfe: Du drückst einen Knopf und hörst einen ganzen Durakkord. L2 und L4 sind Grundbässe. Die untere Notenzeile zeigt eine schematische Leselage; ihre Oktaven bilden nicht jeden Stimmchor ab. Melodie und Bass müssen immer zur selben Balgrichtung passen.')
    b.para(751,'Nur L1–L4 werden in den fünf Bonusfassungen gebraucht. Bb entspricht dem deutschen B. L5 auf Druck ist A-Dur; die Mollbezeichnung gilt dort für Dm auf Zug.',8.4)
    b.footer('Referenz: Modell 21/8, Tonart C–F · akkordeonshop24.de · eigene Zeichnung und Nummerierung.')
    out=COURSE/'00_Lehrplan_bis_Weihnachten.pdf';b.save(out);return out

def main():
    old.register_fonts()
    lesson_data=json.loads((COURSE/'data/lesson_content.json').read_text())
    if isinstance(lesson_data,list):lesson_data={x['id']:x for x in lesson_data}
    if 'songs' in lesson_data:
        lesson_data=lesson_data['songs']
        if isinstance(lesson_data,list):lesson_data={x['id']:x for x in lesson_data}
    songs=[]
    for idx,id in enumerate(IDS,1):
        srcpath=COURSE/'data/source-morgen-kommt-der-weihnachtsmann.json' if idx==1 else ROOT/f'songs/public-domain/{id}/source.json'
        source=json.loads(srcpath.read_text());raw=normalise_source(source)
        s={'id':id,'index':idx,'title':source['title'],'source_path':str(srcpath.relative_to(ROOT)),
           'source_urls':source['source_urls'],'lesson':lesson_data[id],
           'variants':{name:add_route(raw,name) for name in ['basic','bonus']},
           'exercises':make_exercises(id),'reading_quiz':single_note_quiz(id)}
        for variant,t in s['variants'].items():
            (COURSE/f'materials/{id}_{variant}.musicxml').write_text(musicxml(t,bass=variant=='bonus'))
            midi_file(t,COURSE/f'materials/{id}_{variant}.mid')
        s['audio']=audio_file(s['variants']['basic'],COURSE/f'audio/{id}_48bpm.mp3')
        s['pdf_path']=workbook(s,lesson_data[id]);songs.append(s)
        bs=s['variants']['basic']['stats'];ps=s['variants']['bonus']['stats']
        print(f"{id}: basic {bs['reversals']} / bonus {ps['reversals']} reversals; {ps['bass_pulses']} bass pulses",flush=True)
    collection={'schema_version':1,'created':'2026-11-23','deadline':'2026-12-24','tempo_unit':'quarter',
        'practice_tempo_bpm':TEMPO,'bonus_max_sounding_quarters_per_direction':CAP,
        'cap_resets_at_rests':False,'songs':songs}
    (COURSE/'data/course.json').write_text(json.dumps(collection,ensure_ascii=False,indent=2)+'\n')
    plan=plan_pdf(songs);combined=fitz.open(plan);toc=[[1,'Lehrplan und Grundlagen',1]]
    for s in songs:
        toc.append([1,f"{s['index']:02d} {s['title']}",len(combined)+1]);combined.insert_pdf(fitz.open(ROOT/s['pdf_path']))
    combined.set_toc(toc);combined.set_metadata({'title':'Fünf Weihnachtslieder - Anfängerkurs Hohner Corso C/F','author':'Akkordeon C/F Lernkurs'})
    combined.save(ROOT/'downloads/Weihnachtskurs_Hohner_CF.pdf',garbage=4,deflate=True)
    print(f'Course: {len(songs)} workbooks, {len(combined)} pages combined',flush=True)

if __name__=='__main__':main()
