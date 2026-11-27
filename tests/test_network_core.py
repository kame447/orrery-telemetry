"""Shared geometry/force behavior, independent of either page's adapters."""
import json
import math
from pathlib import Path
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
SVG_DOM="""
class El{
 constructor(t,a={}){this.tag=t;this.attrs=a;this.children=[];this.dataset={};}
 setAttribute(k,v){this.attrs[k]=String(v);}
 appendChild(e){if(e.parent)e.parent.children=e.parent.children.filter(x=>x!==e);this.children.push(e);e.parent=this;return e;}
 querySelectorAll(){return [];}
 addEventListener(){}
}
global.document={createElementNS:(_,t)=>new El(t)};
"""


def node(code):
    result=subprocess.run(['node','-e',"const c=require('./dashboard/network_core.js');\n"+code],cwd=ROOT,capture_output=True,text=True,check=True,timeout=10)
    return json.loads(result.stdout)


def test_fixed_nodes_spring_damping_and_view_fit():
    got=node("""
const map=new Map([['A',{x:100,y:100,vx:0,vy:0,fixed:true}],['B',{x:320,y:100,vx:0,vy:0,fixed:false}]]);
const move=c.step(map,[{source:'A',target:'B',count:7}],[],{w:600,h:400},{L:100,KR:0,GR:0,KS:.1});
const fit=c.fit(new Map([['A',{x:100,y:100}],['B',{x:200,y:100}]]),{w:600,h:400});
console.log(JSON.stringify({a:map.get('A'),b:map.get('B'),move,fit,empty:c.fit(new Map(),{w:600,h:400})??null}));
""")
    assert got['a']=={'x':100,'y':100,'vx':0,'vy':0,'fixed':True}
    assert abs(got['b']['x']-309.68)<1e-10
    assert abs(got['move']-10.32)<1e-10
    assert got['fit']=={'k':2,'vx':0,'vy':0} and got['empty'] is None


def test_pair_repel_is_symmetric_and_cooling_reduces_motion():
    got=node("""
function run(cool){const m=new Map([['A',{x:100,y:100,vx:0,vy:0}],['B',{x:110,y:100,vx:0,vy:0}]]);c.step(m,[],[],{w:600,h:400},{L:100,KR:1000,GR:0,KS:0},cool);return [...m.values()];}
console.log(JSON.stringify({normal:run(1),cooled:run(.7)}));
""")
    assert abs(got['normal'][0]['x']-91.4)<1e-10
    assert abs(got['normal'][1]['x']-118.6)<1e-10
    assert got['cooled'][0]['x']>got['normal'][0]['x']


def test_directed_mail_counts_and_attention_foreground_are_shared():
    got=node(SVG_DOM+"""
const svg=new El('svg'),map=new Map([['A',{state:'finished'}],['B',{state:'run',actState:'ask'}],['C',{state:'run',actState:'work'}],['D',{retired:true}]]);
const els=c.buildElements(svg,map,[{source:'A',target:'B',count:1},{source:'B',target:'A',count:7},{source:'A',target:'C',count:3}],[{source:'A',target:'B'},{source:'B',target:'C'}],{widthMultiplier:1,render:(n,g)=>new El('g',{name:n,role:g.role})});
console.log(JSON.stringify({widths:els.edge.slice(0,3).map(e=>e.ln.attrs['stroke-width']),arrows:els.edge.map(e=>e.ln.attrs['marker-end']||null),counts:els.badge.map(x=>x.tx.textContent),nodes:svg.children.filter(e=>e.tag==='g').map(e=>e.attrs)}));
""")
    assert got['widths']==['2.00','4.00','3.00']
    assert got['arrows']==['url(#mk-c)']*3+[None,None]
    assert got['counts']==[8,3]
    assert got['nodes']==[{'name':'A','role':'parent'},{'name':'D','role':'none'},
                          {'name':'C','role':'child'},{'name':'B','role':'both'}]



def test_arrow_dimensions_do_not_follow_message_count_or_link_width():
    got=node(SVG_DOM+"""
const results=[];
for(const [count,widthMultiplier] of [[1,.5],[7,1],[31,1],[4095,3]]){
 const svg=new El('svg');c.createDefs(svg);
 const map=new Map([['A',{x:100,y:100,state:'run'}],['B',{x:300,y:100,state:'run'}]]);
 const els=c.buildElements(svg,map,[{source:'A',target:'B',count}],[],{widthMultiplier,render:()=>new El('g')});
 c.paint(map,els,28);
 const edge=els.edge[0],coords=el=>['x1','y1','x2','y2'].map(k=>Number(el.attrs[k]));
 results.push({width:edge.ln.attrs['stroke-width'],linecap:edge.ln.attrs['stroke-linecap'],line:coords(edge.ln),hit:coords(edge.hit),badge:[Number(els.badge[0].tx.attrs.x),Number(els.badge[0].tx.attrs.y)],markers:svg.children[0].children.filter(e=>e.tag==='marker').map(e=>e.attrs)});
}
console.log(JSON.stringify(results));
""")
    assert [r['width'] for r in got]==['1.00','4.00','6.00','18.00']
    for result in got:
        assert result['linecap']=='butt'
        assert result['line'][:2]==result['hit'][:2]
        assert result['hit'][2]-result['line'][2]==pytest.approx(6.5)
        assert result['badge']==pytest.approx([(result['hit'][0]+result['hit'][2])/2,100])
        assert len(result['markers'])==2
        for marker in result['markers']:
            assert marker['markerUnits']=='userSpaceOnUse'
            assert marker['markerWidth']==marker['markerHeight']=='6.5'
            assert marker['viewBox']=='0 0 10 10'
            assert marker['refX']=='0' and marker['refY']=='5'


@pytest.mark.parametrize('size',[6,13,28])
def test_arrow_clearance_preserves_portrait_boundaries_hits_and_badges(size):
    got=node(SVG_DOM+"""
const svg=new El('svg'),map=new Map([['A',{x:100,y:100,state:'run'}],['B',{x:300,y:250,state:'finished'}]]);
const els=c.buildElements(svg,map,[{source:'A',target:'B',count:4095},{source:'B',target:'A',count:7}],[{source:'A',target:'B'}],{widthMultiplier:3,render:()=>new El('g')});
c.paint(map,els,"""+str(size)+""");
const coords=el=>['x1','y1','x2','y2'].map(k=>Number(el.attrs[k]));
console.log(JSON.stringify({edges:els.edge.map(e=>coords(e.ln)),hits:els.edge.map(e=>coords(e.hit)),badge:els.badge.map(e=>[Number(e.tx.attrs.x),Number(e.tx.attrs.y)])}));
""")
    forward,reverse,spawn=got['edges']
    x1,y1,x2,y2=got['hits'][0]
    assert math.hypot(x1-100,y1-100)==pytest.approx((13*1.2+2)*size/13)
    assert math.hypot(x2-300,y2-250)==pytest.approx((13*.78+2)*size/13)
    assert (x1-100)*150==pytest.approx((y1-100)*200)
    assert (300-x2)*150==pytest.approx((250-y2)*200)
    assert forward==pytest.approx([x1+6.5*.8,y1+6.5*.6,x2-6.5*.8,y2-6.5*.6])
    assert reverse==pytest.approx([forward[2],forward[3],forward[0],forward[1]])
    assert spawn==got['hits'][0]==got['hits'][2]
    assert got['hits'][1]==pytest.approx([x2,y2,x1,y1])
    assert len(got['badge'])==1
    assert got['badge'][0]==pytest.approx([(x1+x2)/2,(y1+y2)/2])


def test_coincident_and_overlapping_nodes_have_finite_nonreversed_edges():
    got=node(SVG_DOM+"""
const results=[];
for(const [dx,dy] of [[0,0],[1e-10,-1e-10],[3,4]]){
 const svg=new El('svg'),map=new Map([['A',{x:100,y:100,state:'run'}],['B',{x:100+dx,y:100+dy,state:'run'}]]);
 const els=c.buildElements(svg,map,[{source:'A',target:'B',count:1}],[],{widthMultiplier:1,render:()=>new El('g')});
 c.paint(map,els,28);
 const coords=el=>['x1','y1','x2','y2'].map(k=>Number(el.attrs[k]));
 results.push({edge:coords(els.edge[0].ln),hit:coords(els.edge[0].hit),marker:els.edge[0].ln.attrs['marker-end'],badge:[Number(els.badge[0].tx.attrs.x),Number(els.badge[0].tx.attrs.y)]});
}
console.log(JSON.stringify(results));
""")
    for result in got:
        assert all(isinstance(v,(int,float)) and math.isfinite(v) for v in result['edge']+result['badge'])
        x1,y1,x2,y2=result['edge']
        assert [x1,y1]==pytest.approx([x2,y2])
        assert result['hit']==result['edge']
        assert result['marker']=='none'
        assert result['badge']==pytest.approx([(x1+x2)/2,(y1+y2)/2])


@pytest.mark.parametrize('reciprocal',[False,True])
def test_short_edges_hide_and_restore_heads_without_reversing_shafts(reciprocal):
    got=node(SVG_DOM+"""
const reciprocal="""+json.dumps(reciprocal)+""";
const svg=new El('svg'),map=new Map([['A',{x:100,y:100,retired:true}],['B',{x:150,y:100,retired:true}]]);
const edges=['to','cc','bcc'].map(kind=>({source:'A',target:'B',count:4095,kind}));
if(reciprocal)edges.push({source:'B',target:'A',count:31,kind:'to'});
const els=c.buildElements(svg,map,edges,[],{widthMultiplier:3,render:()=>new El('g')});
const coords=el=>['x1','y1','x2','y2'].map(k=>Number(el.attrs[k])),results=[];
for(const free of [20,0,6,6.5,7,12,13,14,20]){
 map.get('B').x=130+free;
 c.paint(map,els,13);
 results.push({free,edges:els.edge.map(e=>({line:coords(e.ln),hit:coords(e.hit),marker:e.ln.attrs['marker-end']}))});
}
console.log(JSON.stringify(results));
""")
    for frame in got:
        arrow=frame['free']>6.5*(2 if reciprocal else 1)
        for edge in frame['edges']:
            x1,y1,x2,y2=edge['line']
            hx1,hy1,hx2,hy2=edge['hit']
            sign=1 if hx2>=hx1 else -1
            assert (x2-x1)*sign>=0
            assert y1==y2==hy1==hy2==100
            assert abs(hx2-hx1)==pytest.approx(frame['free'])
            assert edge['marker']==('url(#mk-c)' if arrow else 'none')
            assert (hx2-x2)*sign==pytest.approx(6.5 if arrow else 0)
            assert (x1-hx1)*sign==pytest.approx(6.5 if arrow and reciprocal else 0)
