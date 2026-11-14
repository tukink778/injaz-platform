(function(){
  var d=document,root=d.documentElement;
  function $(s,c){return (c||d).querySelector(s)}function $$(s,c){return Array.prototype.slice.call((c||d).querySelectorAll(s))}
  function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){}}
  /* ---- theme / direction / sidebar ---- */
  function toggleTheme(){var cur=root.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');var n=cur==='dark'?'light':'dark';root.setAttribute('data-theme',n);store('theme',n)}
  function toggleDir(){var n=root.getAttribute('dir')==='rtl'?'ltr':'rtl';root.setAttribute('dir',n);root.setAttribute('lang',n==='ltr'?'en':'ar');store('dir',n)}
  function collapse(){if(innerWidth<=980)return;d.body.classList.toggle('collapsed');store('collapsed',d.body.classList.contains('collapsed')?'1':'0')}
  /* ---- confirm dialog ---- */
  var dlg=$('#dlg-confirm'),pending=null,bypass=false;
  function ask(msg,then){if(!dlg||!dlg.showModal){if(confirm(msg))then();return}$('#dc-m').textContent=msg;pending=then;dlg.showModal();var y=$('[data-yes]',dlg);y&&y.focus()}
  if(dlg){dlg.addEventListener('click',function(e){if(e.target.closest('[data-yes]')){var f=pending;pending=null;dlg.close();f&&f()}else if(e.target.closest('[data-no]')||e.target===dlg){pending=null;dlg.close()}})}
  var hd=$('#dlg-help');if(hd)hd.addEventListener('click',function(e){if(e.target.closest('[data-no]')||e.target===hd)hd.close()});
  /* ---- notifications / search panels ---- */
  function closePanels(except){$$('.drop-panel').forEach(function(p){if(p!==except)p.hidden=true});var b=$('[data-nt]');if(b&&(!except||except!==b.nextElementSibling))b.setAttribute('aria-expanded','false')}
  d.addEventListener('click',function(e){
    var t=e.target;
    if(t.closest('[data-theme-toggle]'))toggleTheme();
    if(t.closest('[data-dir-toggle]'))toggleDir();
    if(t.closest('[data-collapse]'))collapse();
    if(t.closest('[data-help]')&&hd)hd.showModal();
    var m=t.closest('[data-menu]');if(m){var s=$('.side');s.classList.toggle('open');m.setAttribute('aria-expanded',s.classList.contains('open'))}
    else if(innerWidth<=980&&!t.closest('.side')){var sd=$('.side');sd&&sd.classList.remove('open')}
    var nt=t.closest('[data-nt]');if(nt){var p=nt.nextElementSibling;var show=p.hidden;closePanels(show?p:null);p.hidden=!show;nt.setAttribute('aria-expanded',show)}
    else if(!t.closest('.drop-panel')&&!t.closest('.gsearch'))closePanels();
    var x=t.closest('.toast [data-x]');if(x)x.parentNode.remove();
    var cp=t.closest('[data-copy]');if(cp){navigator.clipboard&&navigator.clipboard.writeText(cp.getAttribute('data-copy'));var o=cp.textContent;cp.textContent='تم النسخ ✓';setTimeout(function(){cp.textContent=o},1500)}
    var ep=t.closest('[data-edit-part]');if(ep){var dg=$('#dlg-edit');if(dg&&dg.showModal){var fm=$('form',dg);fm.action=ep.getAttribute('data-action');['name','name_en','uid','email','spec','acad','gender','nat'].forEach(function(k){if(fm.elements[k])fm.elements[k].value=ep.getAttribute('data-'+k)||''});dg.showModal()}}
    if(t.closest('[data-dlg-close]')){var dd=t.closest('dialog');dd&&dd.close()}
    if(t.closest('[data-print]'))window.print();
    var c=t.closest('[data-confirm]');
    if(c&&c.tagName!=='FORM'&&!bypass){e.preventDefault();var btn=c;ask(c.getAttribute('data-confirm'),function(){bypass=true;if(btn.form&&btn.type!=='button'){btn.form.requestSubmit(btn)}else btn.click();bypass=false})}
  });
  d.addEventListener('submit',function(e){
    var f=e.target;
    var c=f.getAttribute&&f.getAttribute('data-confirm');
    if(c&&!bypass){e.preventDefault();ask(c,function(){bypass=true;f.requestSubmit?f.requestSubmit():f.submit();bypass=false});return}
    if(f.hasAttribute('data-nolock')||(f.getAttribute('method')||'get').toLowerCase()==='get'&&!f.hasAttribute('data-lock'))return;
    if(f.dataset.sent){e.preventDefault();return}
    f.dataset.sent='1';var sb=e.submitter;
    setTimeout(function(){$$('button:not([type=button]),input[type=submit]',f).forEach(function(b){b.setAttribute('aria-busy','true')})},0);
  });
  addEventListener('pageshow',function(){$$('form[data-sent]').forEach(function(f){delete f.dataset.sent;$$('[aria-busy]',f).forEach(function(b){b.removeAttribute('aria-busy')})})});
  d.addEventListener('change',function(e){
    var a=e.target.closest('[data-checkall]');
    if(a){$$(a.getAttribute('data-checkall')).forEach(function(x){x.checked=a.checked});updSel()}
    if(e.target.matches('.rowchk'))updSel();
    var s=e.target.closest('[data-autosubmit]');if(s){s.form.submit()}
    var f=e.target.closest('input[type=file][data-drop]');if(f&&f.files[0]){var l=$('[data-filename]');if(l)l.textContent=f.files[0].name}
  });
  function updSel(){var n=$$('.rowchk:checked').length;var el=$('[data-selcount]');if(el)el.textContent=n;$$('[data-needsel]').forEach(function(b){b.disabled=!n})}
  updSel();
  /* ---- toasts auto-dismiss ---- */
  $$('.toast').forEach(function(t){if(!t.classList.contains('err'))setTimeout(function(){t.style.transition='.4s';t.style.opacity=0;setTimeout(function(){t.remove()},400)},6000)});
  /* ---- drag & drop upload ---- */
  var dz=$('.drop');
  if(dz){['dragenter','dragover'].forEach(function(n){dz.addEventListener(n,function(e){e.preventDefault();dz.classList.add('hov')})});
    ['dragleave','drop'].forEach(function(n){dz.addEventListener(n,function(e){e.preventDefault();dz.classList.remove('hov')})});
    dz.addEventListener('drop',function(e){var i=$('input[type=file]',dz);i.files=e.dataTransfer.files;i.dispatchEvent(new Event('change',{bubbles:true}))})}
  /* ---- job progress ---- */
  var jb=$('[data-job]');
  if(jb){var url=jb.getAttribute('data-job');(function poll(){fetch(url,{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
      var pc=j.total?Math.round(100*j.done/j.total):100;$('.progress i').style.width=pc+'%';var pe=$('[data-jpct]');if(pe)pe.textContent=pc+'%';
      $('[data-jdone]').textContent=j.done;$('[data-jfail]').textContent=j.failed;
      if(j.status==='running'){setTimeout(poll,900)}else{$('[data-jmsg]').textContent=j.message;$('[data-jback]').hidden=false;$('[data-jspin]').hidden=true}
    }).catch(function(){setTimeout(poll,2000)})})()}
  var ts=$('#tplsel');if(ts){ts.addEventListener('change',function(){var i=$('#tplprev');i.src=i.src.replace(/templates\/\d+\//,'templates/'+ts.value+'/')})}
  var pv=$('[data-live-preview]');
  if(pv){var tm0;pv.addEventListener('input',function(){clearTimeout(tm0);tm0=setTimeout(function(){var fd=new FormData(pv);var img=$('#prev');img.style.opacity=.4;
    fetch(pv.getAttribute('data-live-preview'),{method:'POST',body:fd,credentials:'same-origin'}).then(function(r){return r.blob()}).then(function(b){img.src=URL.createObjectURL(b);img.style.opacity=1})},700)})}
  /* ---- global search ---- */
  var gs=$('#gs'),gr=$('#gsr'),tm,seq=0;
  function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
  if(gs&&gr){
    gs.addEventListener('input',function(){clearTimeout(tm);var q=gs.value.trim();if(q.length<2){gr.hidden=true;return}
      gr.hidden=false;gr.innerHTML='<span class="skel"></span><span class="skel" style="width:70%"></span><span class="skel" style="width:85%"></span>';
      var my=++seq;tm=setTimeout(function(){fetch('/api/search?q='+encodeURIComponent(q),{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
        if(my!==seq)return;var h='';
        function sec(t,a,k){if(a.length){h+='<div class="h">'+t+'</div>';a.forEach(function(x){h+='<a class="it" href="'+esc(x.u)+'"><span><b>'+esc(x.t)+'</b>'+(x.d?'<br><span class="small muted">'+esc(x.d)+'</span>':'')+'</span></a>'})}}
        sec('صفحات',j.pages);sec('فعاليات',j.events);sec('شهادات ومشاركون',j.people);
        gr.innerHTML=h||'<div class="empty" style="padding:18px"><div class="small">لا نتائج. اضغط Enter للبحث الكامل.</div></div>'}).catch(function(){gr.hidden=true})},250)});
    gs.addEventListener('keydown',function(e){var its=$$('a.it',gr);var i=its.findIndex(function(x){return x.classList.contains('act')});
      if(e.key==='ArrowDown'||e.key==='ArrowUp'){if(!its.length)return;e.preventDefault();if(i>=0)its[i].classList.remove('act');i=e.key==='ArrowDown'?(i+1)%its.length:(i<=0?its.length-1:i-1);its[i].classList.add('act')}
      else if(e.key==='Enter'&&i>=0){e.preventDefault();location.href=its[i].getAttribute('href')}
      else if(e.key==='Escape'){gr.hidden=true;gs.blur()}})}
  /* ---- keyboard shortcuts ---- */
  var gpend=0;
  d.addEventListener('keydown',function(e){
    var t=e.target,typing=t.matches&&t.matches('input,textarea,select,[contenteditable]');
    if(e.key==='Escape'){closePanels();var s=$('.side');s&&s.classList.remove('open');return}
    if(typing||e.ctrlKey||e.metaKey||e.altKey||$('.ed'))return;
    if(gpend){gpend=0;var map={d:'/',e:'/events',a:'/archive',s:'/stats'};if(map[e.key]){e.preventDefault();location.href=map[e.key]}return}
    if(e.key==='/'){if(gs){e.preventDefault();gs.focus()}}
    else if(e.key==='g'){gpend=1;setTimeout(function(){gpend=0},1200)}
    else if(e.key==='n'&&$('.side')){location.href='/events/new'}
    else if(e.key==='t')toggleTheme();
    else if(e.key==='['&&$('.side'))collapse();
    else if(e.key==='?'&&hd)hd.showModal();
  });
  var df=$('form[data-draft]');
  if(df){var key='draft-'+df.getAttribute('data-draft'),ntf=$('#draftnote');
    try{var sv=JSON.parse(localStorage.getItem(key)||'null');if(sv){Object.keys(sv).forEach(function(k){var f=df.elements[k];if(f&&f.type!=='hidden'&&f.type!=='file'&&!f.value){if(f.type==='checkbox')f.checked=sv[k]==='on';else f.value=sv[k]}});if(ntf){ntf.hidden=false;ntf.textContent='استُعيدت مسودتك المحفوظة'}}}catch(e){}
    var dt;df.addEventListener('input',function(){clearTimeout(dt);dt=setTimeout(function(){var o={};Array.prototype.forEach.call(df.elements,function(f){if(f.name&&f.name!=='_csrf'&&f.name!=='next'&&f.type!=='file'&&f.type!=='submit'){if(f.type==='checkbox')o[f.name]=f.checked?'on':'';else o[f.name]=f.value}});try{localStorage.setItem(key,JSON.stringify(o))}catch(e){};if(ntf){ntf.hidden=false;ntf.textContent='حُفظت مسودة تلقائياً'}},500)});
    df.addEventListener('submit',function(){try{localStorage.removeItem(key)}catch(e){}})}
  d.querySelectorAll('[data-autofocus]').forEach(function(x){x.focus()});
  window.INJAZ={ask:ask,$:$,$$:$$,esc:esc};
})();
