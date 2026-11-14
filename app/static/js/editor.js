(function(){
'use strict';
var d=document,$=function(s,c){return(c||d).querySelector(s)},$$=function(s,c){return Array.prototype.slice.call((c||d).querySelectorAll(s))};
var root=$('.ed');if(!root)return;
var cfg=JSON.parse($('#cfgdata').textContent),PH=JSON.parse($('#phdata').textContent);
var cv=$('#cv'),props=$('#props'),layersEl=$('#layers'),TID=root.dataset.tid,CSRF=root.dataset.csrf;
var SAMPLE={name:'اسم المشارك',name_ar:'اسم المشارك',name_en:'Participant Name',uid:'00000000',specialization:'اسم التخصص',academic_status_ar:'طالب',academic_status_en:'Student',gender_ar:'ذكر',gender_en:'Male',nationality_ar:'عماني',nationality_en:'Omani',event_ar:'عنوان الفعالية',event_en:'Event Title',date_ar:'1 يناير 2026',date_en:'Jan 1, 2026',date_from_en:'Jan 1, 2026',date_to_en:'Jan 5, 2026',hours:'10',dept_ar:'اسم القسم',dept_en:'Department Name',serial:'SAMPLE-2026-00001',signatory:'اسم الموقّع',signatory_title_ar:'صفة الموقّع',signatory_title_en:'Signatory Title',location:'المكان',issue_date_ar:'1 يناير 2026',issue_date_en:'Jan 1, 2026'};
var FONT={plex:'UI,sans-serif',amiri:'AmiriC,serif',inter:'Inter,UI,sans-serif'};
var BUILTIN=[['stamp','ختم'],['signature','توقيع'],['logo_univ','شعار جامعة'],['logo_pixels','شعار بكسلات']];
var TYPE_AR={text:'نص',image:'صورة',qr:'رمز QR',rect:'شكل'};
var sel=null,hist=[],hpos=-1,dirty=false,showRaw=false,seq=100,assets=[];
cfg.elements=cfg.elements||[];cfg.orientation=cfg.orientation||'landscape';cfg.background=cfg.background||'plain';
cfg.elements.forEach(function(e){if(e.type==='image'&&!/^(stamp|signature|logo_univ|logo_pixels)$/.test(e.src)&&assets.indexOf(e.src)<0)assets.push(e.src)});
if(/^[0-9a-f]{32}\.jpg$/.test(cfg.background)){}
function pageW(){return cfg.orientation==='portrait'?595.28:841.89}
function pageH(){return cfg.orientation==='portrait'?841.89:595.28}
function scale(){return cv.clientWidth/pageW()}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
function toast(t,k){var x=d.createElement('div');x.className='toast '+(k||'ok');x.setAttribute('role',k==='err'?'alert':'status');x.innerHTML='<span>'+esc(t)+'</span><button type="button" data-x aria-label="إغلاق">×</button>';$('#toasts').appendChild(x);if(k!=='err')setTimeout(function(){x.remove()},4000)}
function kash(t){var NO='اأإآدذرزوؤءةى',o='';for(var i=0;i<t.length;i++){var c=t[i],n=t[i+1];o+=c;if(n&&c>='ء'&&c<='ي'&&NO.indexOf(c)<0&&n>='ء'&&n<='ي'&&c!==' '&&n!==' ')o+='ـــ'}return o}
function fillTxt(t,e){var s=String(t||'');if(e&&e.kashida)s=kash(s);return esc(s).replace(/\{(\w+)\}/g,function(m,k){return showRaw?m:(SAMPLE[k]!==undefined?SAMPLE[k]:m)})}
function byId(id){return cfg.elements.filter(function(e){return e.id===id})[0]}
function nid(){return 'e'+(++seq)+Math.floor(Math.random()*99)}
/* ---------- history / dirty ---------- */
function snap(){return JSON.stringify(cfg)}
function push(){var s=snap();if(hist[hpos]===s)return;hist=hist.slice(0,hpos+1);hist.push(s);if(hist.length>80)hist.shift();hpos=hist.length-1;mark()}
function mark(){dirty=true;$('#dirty').classList.add('on');try{localStorage.setItem('tpl-draft-'+TID,JSON.stringify({t:Date.now(),name:$('#tname').value,cfg:cfg}))}catch(e){}}
function undo(){if(hpos>0){cfg=JSON.parse(hist[--hpos]);after()}}
function redo(){if(hpos<hist.length-1){cfg=JSON.parse(hist[++hpos]);after()}}
function after(){if(sel&&!byId(sel))sel=null;draw();drawProps();drawLayers()}
/* ---------- canvas drawing ---------- */
function bgStyle(){
  var b=cfg.background;cv.style.aspectRatio=pageW()+'/'+pageH();cv.style.background='#fff';
  var old=$('.bgimg',cv);if(old)old.remove();
  if(/^#/.test(b))cv.style.background=b;
  else if(b==='official'&&cfg.orientation!=='portrait'){var i=new Image();i.className='bgimg';i.src='/static/img/cert_bg.jpg';i.alt='';cv.insertBefore(i,cv.firstChild)}
  else if(/^[0-9a-f]{32}\.jpg$/.test(b)){var j=new Image();j.className='bgimg';j.src='/admin/assets/'+b;j.alt='';cv.insertBefore(j,cv.firstChild)}
}
function elHtml(e,sc){
  if(e.type==='text'){var fs=e.size*sc,jc={top:'flex-start',middle:'center',bottom:'flex-end'}[e.valign||'middle'];
    var h=fillTxt(e.text,e);if(e.upper)h=h.toUpperCase();
    return '<div class="in" style="display:flex;flex-direction:column;justify-content:'+jc+';font-family:'+FONT[e.font||'plex']+';font-size:'+fs+'px;font-weight:'+(e.weight||500)+';color:'+e.color+';text-align:'+e.align+';direction:'+e.dir+';line-height:'+e.lh+';letter-spacing:'+((e.ls||0)*.75*sc)+'px"><div style="white-space:pre-wrap">'+h+'</div></div>'}
  if(e.type==='image'){var src=/^(stamp|signature|logo_univ|logo_pixels)$/.test(e.src)?'/static/img/'+e.src+'.png':'/admin/assets/'+e.src;return '<div class="in" style="opacity:'+e.opacity+'"><img src="'+src+'" alt=""></div>'}
  if(e.type==='qr')return '<div class="qrph"><svg viewBox="0 0 10 10" fill="#111"><path d="M0 0h4v4H0zM6 0h4v4H6zM0 6h4v4H0zM1 1h2v2H1zM7 1h2v2H7zM1 7h2v2H1zM6 6h2v2H6zM9 6h1v2H9zM6 9h2v1H6zM8 8h2v2H8z" fill-rule="evenodd"/></svg>'+(e.caption?'<div style="font:600 '+(7.5*sc)+'px Inter,monospace;direction:ltr;color:#333">'+SAMPLE.serial+'</div>':'')+'</div>';
  return '<div class="in" style="background:'+e.fill+';opacity:'+e.opacity+';border-radius:'+(e.radius*2.835*sc)+'px;'+(e.border?'border:'+(e.border*2.835*sc)+'px solid '+e.bcolor:'')+'"></div>';
}
function place(div,e){div.style.left=e.x+'%';div.style.top=e.y+'%';div.style.width=e.w+'%';div.style.height=e.h+'%'}
function draw(){
  bgStyle();$$('.el,.gl',cv).forEach(function(n){n.remove()});var sc=scale();
  cfg.elements.forEach(function(e){var n=d.createElement('div');n.className='el'+(e.id===sel?' sel':'');n.dataset.id=e.id;place(n,e);n.innerHTML=elHtml(e,sc);
    if(e.id===sel)['nw','ne','sw','se'].forEach(function(p){var h=d.createElement('i');h.className='hd '+p;h.dataset.h=p;n.appendChild(h)});cv.appendChild(n)});
}
function drawLayers(){
  layersEl.innerHTML=cfg.elements.slice().reverse().map(function(e){var lab=e.type==='text'?(e.text||'').slice(0,22)||'نص فارغ':TYPE_AR[e.type];
    return '<div class="layer'+(e.id===sel?' sel':'')+'" data-id="'+e.id+'"><span><b>'+TYPE_AR[e.type]+'</b> · '+esc(lab)+'</span><button type="button" data-up title="للأمام" aria-label="للأمام">▲</button><button type="button" data-dn title="للخلف" aria-label="للخلف">▼</button></div>'}).join('')||'<p class="hint">لا توجد عناصر. أضف نصاً أو صورة.</p>';
}
/* ---------- properties ---------- */
function fld(l,inner,cls){return '<div class="fld '+(cls||'')+'"><label>'+l+'</label>'+inner+'</div>'}
function inp(k,v,t,ex){return '<input data-p="'+k+'" type="'+(t||'number')+'" value="'+esc(v)+'" '+(ex||'')+'>'}
function sel_(k,v,opts){return '<select data-p="'+k+'">'+opts.map(function(o){return '<option value="'+o[0]+'"'+(String(o[0])===String(v)?' selected':'')+'>'+o[1]+'</option>'}).join('')+'</select>'}
function seg(k,v,opts){return '<div class="seg" role="group">'+opts.map(function(o){return '<button type="button" data-seg="'+k+'" data-v="'+o[0]+'" class="'+(o[0]===v?'on':'')+'">'+o[1]+'</button>'}).join('')+'</div>'}
function drawProps(){
  var e=sel&&byId(sel),h='';
  if(!e){
    h='<h3>إعدادات الصفحة</h3>'+fld('اتجاه الصفحة',sel_('orientation',cfg.orientation,[['landscape','A4 أفقي'],['portrait','A4 عمودي']]))
    +fld('الخلفية',sel_('bgmode',/^#/.test(cfg.background)?'color':(cfg.background==='official'?'official':(/\.jpg$/.test(cfg.background)?'image':'plain')),[['plain','أبيض'],['color','لون موحّد'],['official','خلفية الجامعة الرسمية (أفقي)'],['image','صورة مرفوعة']]))
    +(/^#/.test(cfg.background)?fld('لون الخلفية','<input type="color" data-bgcolor value="'+cfg.background+'">'):'')
    +'<div class="fld"><label>رفع صورة خلفية (A4)</label><input type="file" accept="image/*" data-upbg></div>'
    +'<h3>المتغيرات المتاحة</h3><p class="hint">تُستبدل بالبيانات الحقيقية عند الإصدار:</p><div class="phs">'+PH.map(function(k){return '<button type="button" data-ph="'+k+'">{'+k+'}</button>'}).join('')+'</div>'
    +'<label class="chk" style="margin-top:12px"><input type="checkbox" id="rawph"'+(showRaw?' checked':'')+'> عرض أسماء المتغيرات بدل البيانات التجريبية</label>';
    props.innerHTML=h;return}
  h='<h3>'+TYPE_AR[e.type]+'</h3><div class="pg">'+fld('X %',inp('x',e.x,'number','step="0.5"'))+fld('Y %',inp('y',e.y,'number','step="0.5"'))+fld('العرض %',inp('w',e.w,'number','step="0.5"'))+fld('الارتفاع %',inp('h',e.h,'number','step="0.5"'))+'</div>';
  if(e.type==='text'){
    h+='<h3>النص</h3><div class="fld"><textarea data-p="text" rows="3" style="min-height:70px">'+esc(e.text)+'</textarea><div class="phs">'+PH.slice(0,14).map(function(k){return '<button type="button" data-ins="'+k+'">{'+k+'}</button>'}).join('')+'</div></div>'
    +'<div class="pg">'+fld('الخط',sel_('font',e.font,[['plex','IBM Plex (عصري)'],['amiri','Amiri (أميري)'],['inter','Inter (لاتيني)']]))+fld('الحجم pt',inp('size',e.size,'number','step="0.5" min="5" max="120"'))
    +fld('السُّمك',sel_('weight',e.weight,[[400,'عادي'],[500,'متوسط'],[600,'شبه عريض'],[700,'عريض']]))+fld('اللون','<input type="color" data-p="color" value="'+e.color+'">')
    +fld('الاتجاه',sel_('dir',e.dir,[['rtl','RTL عربي'],['ltr','LTR إنجليزي']]))+fld('تباعد الأسطر',inp('lh',e.lh,'number','step="0.1" min="0.9" max="3"'))+fld('تباعد الحروف',inp('ls',e.ls,'number','step="0.5"'))+'</div>'
    +'<div class="fld"><label>المحاذاة الأفقية</label>'+seg('align',e.align,[['right','يمين'],['center','وسط'],['left','يسار'],['justify','ضبط']])+'</div>'
    +'<div class="fld"><label>المحاذاة العمودية</label>'+seg('valign',e.valign,[['top','أعلى'],['middle','وسط'],['bottom','أسفل']])+'</div>'
    +'<label class="chk"><input type="checkbox" data-p="kashida"'+(e.kashida?' checked':'')+'> تمديد الحروف العربية (كشيدة)</label><label class="chk"><input type="checkbox" data-p="upper"'+(e.upper?' checked':'')+'> أحرف إنجليزية كبيرة</label>';
  }else if(e.type==='image'){
    var opts=BUILTIN.concat(assets.map(function(a,i){return [a,'صورة مرفوعة '+(i+1)]}));
    h+='<h3>الصورة</h3>'+fld('المصدر',sel_('src',e.src,opts))+'<div class="fld"><label>رفع شعار / ختم / توقيع (PNG شفاف مفضّل)</label><input type="file" accept="image/*" data-upimg></div>'+fld('الشفافية',inp('opacity',e.opacity,'range','min="0.05" max="1" step="0.05"'));
  }else if(e.type==='qr'){
    h+='<h3>رمز التحقق</h3><p class="hint">يُولَّد برابط التحقق الخاص بكل شهادة. حافظ على شكل مربع تقريباً.</p><label class="chk"><input type="checkbox" data-p="caption"'+(e.caption?' checked':'')+'> إظهار الرقم التسلسلي تحت الرمز</label>';
  }else{
    h+='<h3>الشكل</h3><div class="pg">'+fld('اللون','<input type="color" data-p="fill" value="'+e.fill+'">')+fld('الشفافية',inp('opacity',e.opacity,'number','step="0.05" min="0.05" max="1"'))+fld('تدوير الزوايا mm',inp('radius',e.radius,'number','step="0.5"'))+fld('حدود mm',inp('border',e.border,'number','step="0.2"'))+fld('لون الحدود','<input type="color" data-p="bcolor" value="'+e.bcolor+'">')+'</div>';
  }
  props.innerHTML=h;
}
/* ---------- actions ---------- */
function add(type){
  var base={id:nid(),type:type,x:30,y:40,w:40,h:10};
  if(type==='text')Object.assign(base,{text:'نص جديد {name}',font:'plex',size:18,weight:500,color:'#14163a',align:'center',dir:'rtl',valign:'middle',lh:1.4,ls:0,kashida:false,upper:false});
  if(type==='image')Object.assign(base,{src:'stamp',w:14,h:20,opacity:1});
  if(type==='qr')Object.assign(base,{w:10.3,h:14.6,caption:true,color:'#111111'});
  if(type==='rect')Object.assign(base,{fill:'#7A4DFF',radius:0,opacity:1,border:0,bcolor:'#29328D',h:1,w:30});
  cfg.elements.push(base);sel=base.id;push();draw();drawProps();drawLayers()}
function selectEl(id){sel=id;draw();drawProps();drawLayers()}
function remove(){if(!sel)return;cfg.elements=cfg.elements.filter(function(e){return e.id!==sel});sel=null;push();draw();drawProps();drawLayers()}
function dup(){var e=sel&&byId(sel);if(!e)return;var c=JSON.parse(JSON.stringify(e));c.id=nid();c.x+=2;c.y+=2;cfg.elements.push(c);sel=c.id;push();draw();drawProps();drawLayers()}
function move(id,dir){var i=cfg.elements.findIndex(function(e){return e.id===id}),j=i+dir;if(j<0||j>=cfg.elements.length)return;var t=cfg.elements[i];cfg.elements[i]=cfg.elements[j];cfg.elements[j]=t;push();draw();drawLayers()}
$$('[data-add]').forEach(function(b){b.addEventListener('click',function(){add(b.dataset.add)})});
$('#b-undo').onclick=undo;$('#b-redo').onclick=redo;$('#b-dup').onclick=dup;$('#b-del').onclick=remove;
layersEl.addEventListener('click',function(ev){var l=ev.target.closest('.layer');if(!l)return;if(ev.target.closest('[data-up]'))move(l.dataset.id,1);else if(ev.target.closest('[data-dn]'))move(l.dataset.id,-1);else selectEl(l.dataset.id)});
/* ---------- drag / resize ---------- */
var drag=null;
cv.addEventListener('pointerdown',function(ev){
  var h=ev.target.closest('.hd'),n=ev.target.closest('.el');
  if(!n){if(sel){sel=null;draw();drawProps();drawLayers()}return}
  var id=n.dataset.id;if(id!==sel){sel=id;draw();drawProps();drawLayers();n=$('.el[data-id="'+id+'"]',cv);h=null}
  var e=byId(id),r=cv.getBoundingClientRect();
  drag={id:id,mode:h?h.dataset.h:'move',sx:ev.clientX,sy:ev.clientY,o:{x:e.x,y:e.y,w:e.w,h:e.h},r:r,alt:ev.altKey};
  cv.setPointerCapture(ev.pointerId);ev.preventDefault()});
cv.addEventListener('pointermove',function(ev){
  if(!drag)return;var e=byId(drag.id),dx=(ev.clientX-drag.sx)/drag.r.width*100,dy=(ev.clientY-drag.sy)/drag.r.height*100,o=drag.o,sn=ev.altKey?0.01:0.5;
  function R(v){return Math.round(v/sn)*sn}
  if(drag.mode==='move'){e.x=R(o.x+dx);e.y=R(o.y+dy);
    var cx=e.x+e.w/2,cy=e.y+e.h/2;$$('.gl',cv).forEach(function(g){g.remove()});
    if(!ev.altKey){if(Math.abs(cx-50)<.8){e.x=R(50-e.w/2);addGuide('v')}if(Math.abs(cy-50)<.8){e.y=R(50-e.h/2);addGuide('h')}}}
  else{var m=drag.mode;
    if(m.indexOf('e')>=0)e.w=Math.max(1,R(o.w+dx)); if(m.indexOf('s')>=0)e.h=Math.max(.3,R(o.h+dy));
    if(m.indexOf('w')>=0){var nw=Math.max(1,R(o.w-dx));e.x=R(o.x+o.w-nw);e.w=nw}
    if(m.indexOf('n')>=0){var nh=Math.max(.3,R(o.h-dy));e.y=R(o.y+o.h-nh);e.h=nh}}
  var n=$('.el[data-id="'+e.id+'"]',cv);place(n,e);if(e.type==='text'||e.type==='qr')n.firstChild.outerHTML=elHtml(e,scale());syncPos(e)});
function addGuide(k){var g=d.createElement('div');g.className='gl '+k;cv.appendChild(g)}
function endDrag(){if(drag){drag=null;$$('.gl',cv).forEach(function(g){g.remove()});push();draw();drawProps()}}
cv.addEventListener('pointerup',endDrag);cv.addEventListener('pointercancel',endDrag);
function syncPos(e){['x','y','w','h'].forEach(function(k){var i=$('[data-p="'+k+'"]',props);if(i)i.value=e[k]})}
/* ---------- property edits ---------- */
function setProp(e,k,v){
  if(['x','y','w','h','size','lh','ls','opacity','radius','border'].indexOf(k)>=0){v=parseFloat(v);if(isNaN(v))return}
  if(k==='weight')v=parseInt(v,10);e[k]=v}
var ptm;
props.addEventListener('input',function(ev){
  var t=ev.target,k=t.dataset.p;
  if(t.id==='rawph'){return}
  if(t.dataset.bgcolor!==undefined){cfg.background=t.value;draw();clearTimeout(ptm);ptm=setTimeout(push,300);return}
  if(!k)return;var e=sel&&byId(sel);if(!e)return;
  setProp(e,k,t.type==='checkbox'?t.checked:t.value);
  var n=$('.el[data-id="'+e.id+'"]',cv);if(n){place(n,e);var keep=$$('.hd',n);n.innerHTML=elHtml(e,scale());keep.forEach(function(x){n.appendChild(x)})}
  drawLayers();clearTimeout(ptm);ptm=setTimeout(push,350)});
props.addEventListener('change',function(ev){
  var t=ev.target;
  if(t.id==='rawph'){showRaw=t.checked;draw();return}
  if(t.dataset.p==='orientation'||t.dataset.p==='bgmode'){
    if(!sel){if(t.dataset.p==='orientation'){cfg.orientation=t.value;if(t.value==='portrait'&&cfg.background==='official')cfg.background='plain'}
      else{var v=t.value;cfg.background=v==='plain'?'plain':v==='color'?'#EEF1FB':v==='official'?(cfg.orientation==='portrait'?'plain':'official'):cfg.background;if(v==='image'&&!/\.jpg$/.test(cfg.background))toast('ارفع صورة الخلفية من الحقل أدناه','warn')}
      push();draw();drawProps()}return}
  if(t.dataset.p==='src'||t.dataset.p==='font'||t.dataset.p==='dir'||t.dataset.p==='weight'){drawProps()}
  if(t.dataset.upimg!==undefined||t.dataset.upbg!==undefined){upload(t)}});
props.addEventListener('click',function(ev){
  var b=ev.target.closest('[data-seg]');
  if(b){var e=sel&&byId(sel);if(!e)return;e[b.dataset.seg]=b.dataset.v;push();draw();drawProps();return}
  var p=ev.target.closest('[data-ph]');if(p){toast('انسخ {'+p.dataset.ph+'} وألصقه في نص عنصر. أو حدّد عنصراً نصياً واضغط المتغير.','info');return}
  var i=ev.target.closest('[data-ins]');
  if(i){var e2=sel&&byId(sel),ta=$('textarea[data-p=text]',props);if(!e2||!ta)return;var s=ta.selectionStart||ta.value.length,tok='{'+i.dataset.ins+'}';ta.value=ta.value.slice(0,s)+tok+ta.value.slice(ta.selectionEnd||s);e2.text=ta.value;push();draw();drawLayers();ta.focus();ta.selectionStart=ta.selectionEnd=s+tok.length}});
function upload(inputEl){
  var f=inputEl.files[0];if(!f)return;var fd=new FormData();fd.append('file',f);fd.append('kind',inputEl.dataset.upbg!==undefined?'bg':'img');
  fetch(root.dataset.asset,{method:'POST',body:fd,headers:{'X-CSRF-Token':CSRF},credentials:'same-origin'}).then(function(r){return r.json().then(function(j){return{ok:r.ok,j:j}})}).then(function(x){
    if(!x.ok){toast(x.j.error||'تعذر رفع الصورة','err');return}
    if(inputEl.dataset.upbg!==undefined){cfg.background=x.j.name}
    else{var e=sel&&byId(sel);assets.push(x.j.name);if(e)e.src=x.j.name}
    push();draw();drawProps();toast('تم رفع الصورة')}).catch(function(){toast('تعذر رفع الصورة','err')})}
/* ---------- save / preview / draft ---------- */
function save(){
  var fd=new FormData();fd.append('cfg',JSON.stringify(cfg));fd.append('name',$('#tname').value);fd.append('act','save');
  var b=$('#b-save');b.setAttribute('aria-busy','true');
  fetch(root.dataset.save,{method:'POST',body:fd,headers:{'X-CSRF-Token':CSRF,'X-Requested-With':'fetch'},credentials:'same-origin'}).then(function(r){return r.json()}).then(function(){
    dirty=false;$('#dirty').classList.remove('on');try{localStorage.removeItem('tpl-draft-'+TID)}catch(e){}toast('تم حفظ القالب. الشهادات الصادرة سابقاً لا تتغير')}).catch(function(){toast('تعذر الحفظ. حاول مجدداً','err')}).then(function(){b.removeAttribute('aria-busy')})}
$('#b-save').onclick=save;
$('#b-prev').onclick=function(){
  var dl=$('#dlg-prev'),im=$('#prev-img'),sk=$('#prev-skel');im.hidden=true;sk.hidden=false;dl.showModal();
  var fd=new FormData();fd.append('cfg',JSON.stringify(cfg));
  fetch(root.dataset.live,{method:'POST',body:fd,headers:{'X-CSRF-Token':CSRF},credentials:'same-origin'}).then(function(r){if(!r.ok)throw 0;return r.blob()}).then(function(b){im.src=URL.createObjectURL(b);im.hidden=false;sk.hidden=true}).catch(function(){sk.hidden=true;toast('تعذرت المعاينة','err');dl.close()})};
var dl=$('#dlg-prev');dl.addEventListener('click',function(e){if(e.target.closest('[data-no]')||e.target===dl)dl.close()});
try{var dr=JSON.parse(localStorage.getItem('tpl-draft-'+TID)||'null');if(dr&&dr.cfg&&JSON.stringify(dr.cfg)!==JSON.stringify(cfg)){$('#draftbar').hidden=false;
  $('#d-restore').onclick=function(){cfg=dr.cfg;cfg.elements=cfg.elements||[];$('#tname').value=dr.name||$('#tname').value;$('#draftbar').hidden=true;sel=null;push();draw();drawProps();drawLayers()};
  $('#d-drop').onclick=function(){localStorage.removeItem('tpl-draft-'+TID);$('#draftbar').hidden=true}}}catch(e){}
$('#tname').addEventListener('input',function(){mark()});
addEventListener('beforeunload',function(e){if(dirty){e.preventDefault();e.returnValue=''}});
/* ---------- keyboard ---------- */
d.addEventListener('keydown',function(ev){
  var t=ev.target,typing=t.matches&&t.matches('input,textarea,select');
  if((ev.ctrlKey||ev.metaKey)&&ev.key==='s'){ev.preventDefault();save();return}
  if(typing)return;
  if((ev.ctrlKey||ev.metaKey)&&ev.key==='z'){ev.preventDefault();undo()}
  else if((ev.ctrlKey||ev.metaKey)&&(ev.key==='y'||(ev.shiftKey&&ev.key==='Z'))){ev.preventDefault();redo()}
  else if((ev.ctrlKey||ev.metaKey)&&ev.key==='d'){ev.preventDefault();dup()}
  else if(ev.key==='Delete'||ev.key==='Backspace'){if(sel){ev.preventDefault();remove()}}
  else if(sel&&/^Arrow/.test(ev.key)){ev.preventDefault();var e=byId(sel),st=ev.shiftKey?2:.5;if(ev.key==='ArrowLeft')e.x-=st;if(ev.key==='ArrowRight')e.x+=st;if(ev.key==='ArrowUp')e.y-=st;if(ev.key==='ArrowDown')e.y+=st;draw();syncPos(e);clearTimeout(ptm);ptm=setTimeout(push,300)}
  else if(ev.key==='Escape'){sel=null;draw();drawProps();drawLayers()}},true);
addEventListener('resize',function(){draw()});
/* init */
hist=[snap()];hpos=0;dirty=false;draw();drawProps();drawLayers();
if(d.fonts&&d.fonts.ready)d.fonts.ready.then(draw);
})();
