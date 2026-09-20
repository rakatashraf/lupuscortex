const $ = id => document.getElementById(id);
let selectedCollection = null;
let currentGranules = [];

function toast(message, kind){
  const el=$("toast");
  el.textContent=message;
  el.className="toast show"+(kind?" "+kind:"");
  clearTimeout(window.__toastTimer);
  window.__toastTimer=setTimeout(function(){el.className="toast";},3200);
}
function csvList(value){return (value||"").split(",").map(function(x){return x.trim();}).filter(Boolean);}
function bbox(){return {south:Number($("south").value),west:Number($("west").value),north:Number($("north").value),east:Number($("east").value)};}
function dates(){return {start:$("startDate").value,end:$("endDate").value};}
function validateInputs(requireToken){
  if(requireToken && !$("token").value.trim()) throw new Error("Paste your Earthdata token first.");
  if(!$("component").value.trim()) throw new Error("Enter a component or variable.");
  const b=bbox();
  if(Object.values(b).some(function(v){return !Number.isFinite(v);})) throw new Error("Enter a valid bounding box.");
  if(b.south>=b.north) throw new Error("South must be lower than north.");
  if(b.west>=b.east) throw new Error("West must be lower than east.");
  if(!$("startDate").value || !$("endDate").value) throw new Error("Select both dates.");
  if($("startDate").value>$("endDate").value) throw new Error("Start date must be on or before end date.");
}
async function api(path,body,asBlob){
  const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  if(!response.ok){
    let detail="HTTP "+response.status;
    try{const data=await response.json();detail=data.detail||detail;}catch(e){try{detail=await response.text()||detail;}catch(_){}}
    throw new Error(detail);
  }
  return asBlob?response.blob():response.json();
}
function downloadBlob(blob,name){
  const url=URL.createObjectURL(blob);
  const a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();
  setTimeout(function(){URL.revokeObjectURL(url);},1500);
}
function escapeHtml(value){
  return String(value==null?"":value).replace(/[&<>'"]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c];});
}
function slug(value){return (value||"earthdata").toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^_|_$/g,"");}
function setDefaultDates(){
  const end=new Date();const start=new Date(end.getTime()-29*86400000);
  $("endDate").value=end.toISOString().slice(0,10);$("startDate").value=start.toISOString().slice(0,10);
}
async function health(){
  try{
    const r=await fetch("/api/health");if(!r.ok) throw new Error();
    const d=await r.json();$("health").textContent="Service online · v"+(d.version||"");$("health").className="status ok";
  }catch(e){$("health").textContent="Service unavailable";$("health").className="status bad";}
}

$("validateToken").onclick=async function(){
  const button=$("validateToken"),msg=$("tokenMessage");msg.className="message";msg.textContent="";
  try{
    const token=$("token").value.trim();if(!token) throw new Error("Paste an Earthdata token.");
    button.disabled=true;button.textContent="Validating…";
    const data=await api("/api/token/validate",{token:token},false);
    if(!data.valid) throw new Error(data.message||"NASA rejected this token.");
    msg.className="message success";msg.textContent="Token accepted by NASA CMR.";
  }catch(e){msg.className="message error";msg.textContent=e.message;}
  finally{button.disabled=false;button.textContent="Validate token";}
};

$("searchCollections").onclick=async function(){
  const button=$("searchCollections"),msg=$("searchMessage");
  msg.className="message";msg.textContent="";selectedCollection=null;currentGranules=[];
  $("collectionPanel").classList.add("hidden");$("granulePanel").classList.add("hidden");$("externalArea").classList.add("hidden");
  try{
    validateInputs(true);button.disabled=true;button.textContent="Searching NASA…";
    const body={
      token:$("token").value.trim(),component:$("component").value.trim(),bbox:bbox(),
      platforms:csvList($("platformFilter").value),instruments:csvList($("instrumentFilter").value),
      page_size:Number($("collectionLimit").value)||40
    };
    const data=await api("/api/collections/search",body,false);
    const items=(data.nasa&&data.nasa.items)||[];
    $("collectionCount").textContent=items.length+" shown · "+((data.nasa&&data.nasa.hits)!=null?data.nasa.hits:items.length)+" matches";
    renderCollections(items);$("collectionPanel").classList.remove("hidden");$("collectionPanel").scrollIntoView({behavior:"smooth",block:"start"});
    if(items.length){msg.className="message success";msg.textContent="NASA collections found. Select the product that matches your scientific use case.";}
    else{msg.className="message warn";msg.textContent="No NASA collection matched these filters. Public fallback sources are shown when a mapping exists.";}
    renderExternal(data.external_candidates_always||data.external_candidates||[]);
  }catch(e){msg.className="message error";msg.textContent=e.message;}
  finally{button.disabled=false;button.textContent="Search Earthdata";}
};

function renderCollections(items){
  const root=$("collections");root.innerHTML="";
  if(!items.length){root.innerHTML='<div class="card"><h3>No NASA collection matched</h3><p>Try a broader component term, remove the platform/instrument filter, or use a public fallback below.</p></div>';return;}
  items.forEach(function(item){
    const el=document.createElement("article");el.className="card";
    const chips=(item.platforms||[]).concat(item.instruments||[]).map(function(x){return '<span class="chip">'+escapeHtml(x)+'</span>';}).join("");
    const level=item.processing_level?'<span class="chip">Level '+escapeHtml(item.processing_level)+'</span>':"";
    el.innerHTML='<h3>'+escapeHtml(item.title||item.short_name||item.concept_id)+'</h3><div class="meta">'+chips+level+'</div><p>'+escapeHtml(item.abstract||"No abstract supplied by CMR.")+'</p><p><strong>'+escapeHtml(item.short_name||"")+'</strong> '+(item.version?"· v"+escapeHtml(item.version):"")+'<br>'+escapeHtml(item.temporal_start||"")+(item.temporal_end?" → "+escapeHtml(item.temporal_end):"")+'</p><button class="secondary">Select collection</button>';
    el.querySelector("button").onclick=function(){
      selectedCollection=item;Array.from(root.children).forEach(function(x){x.classList.remove("selected");});el.classList.add("selected");
      $("selectedCollection").innerHTML="<strong>"+escapeHtml(item.title||item.short_name||item.concept_id)+"</strong><br><span>"+escapeHtml(item.concept_id||"")+"</span>";
      $("granulePanel").classList.remove("hidden");$("downloadCsv").disabled=true;currentGranules=[];$("granules").innerHTML="";
      $("granuleMessage").textContent="Collection selected. Click Find granules.";$("granuleMessage").className="message success";
      $("granulePanel").scrollIntoView({behavior:"smooth",block:"start"});
    };
    root.appendChild(el);
  });
}

$("findGranules").onclick=async function(){
  const button=$("findGranules"),msg=$("granuleMessage");if(!selectedCollection){toast("Select a NASA collection first.","error");return;}
  try{
    validateInputs(true);button.disabled=true;button.textContent="Searching granules…";
    const body={
      token:$("token").value.trim(),collection_id:selectedCollection.concept_id,bbox:bbox(),date_range:dates(),
      platform:csvList($("platformFilter").value)[0]||null,instrument:csvList($("instrumentFilter").value)[0]||null,
      max_granules:Number($("maxGranules").value)||5,fallback_latest:$("fallbackLatest").checked
    };
    const data=await api("/api/granules/search",body,false);currentGranules=data.items||[];renderGranules(currentGranules);
    if(data.fallback_used){msg.className="message warn";msg.textContent=data.fallback_reason||"Requested dates were empty, so the newest available granules were used.";}
    else if(currentGranules.length){msg.className="message success";msg.textContent="Found "+currentGranules.length+" downloadable granule(s) in the requested period.";}
    else{msg.className="message error";msg.textContent="No downloadable granules were found.";}
    $("downloadCsv").disabled=!currentGranules.length;
  }catch(e){msg.className="message error";msg.textContent=e.message;}
  finally{button.disabled=false;button.textContent="Find granules";}
};

function renderGranules(items){
  const root=$("granules");root.innerHTML="";
  items.forEach(function(item){
    const el=document.createElement("div");el.className="granule";
    el.innerHTML="<strong>"+escapeHtml(item.granule_ur||item.concept_id||"Granule")+"</strong><span>"+escapeHtml(item.begin||"time unknown")+"</span><span>"+escapeHtml((item.platforms||[]).join(", ")||"platform unknown")+"</span><span>"+(item.size_mb?escapeHtml(String(item.size_mb))+" MB":"")+"</span>";
    root.appendChild(el);
  });
}

$("downloadCsv").onclick=async function(){
  if(!selectedCollection||!currentGranules.length){toast("Find granules first.","error");return;}
  const button=$("downloadCsv");
  try{
    validateInputs(true);button.disabled=true;button.textContent="Downloading + converting…";
    const body={
      token:$("token").value.trim(),component:$("component").value.trim(),collection_id:selectedCollection.concept_id,
      collection_title:selectedCollection.title||selectedCollection.short_name||null,bbox:bbox(),date_range:dates(),
      platform:csvList($("platformFilter").value)[0]||null,instrument:csvList($("instrumentFilter").value)[0]||null,
      max_granules:Number($("maxGranules").value)||5,fallback_latest:$("fallbackLatest").checked,
      variable_filters:csvList($("variableFilters").value),output_name:$("outputName").value.trim()||null,
      max_rows_per_variable:Number($("maxRows").value)||0
    };
    const blob=await api("/api/download/nasa",body,true);
    let name=$("outputName").value.trim()||slug($("component").value)+"_earthdata.csv";if(!name.toLowerCase().endsWith(".csv")) name+=".csv";
    downloadBlob(blob,name);toast("CSV created and sent to your browser.");
  }catch(e){toast(e.message,"error");}
  finally{button.disabled=false;button.textContent="Download combined CSV";}
};

function renderExternal(items){
  if(!items.length) return;$("externalArea").classList.remove("hidden");const root=$("externalCards");root.innerHTML="";
  items.forEach(function(item){
    const el=document.createElement("article");el.className="card";
    el.innerHTML="<h3>"+escapeHtml(item.provider)+"</h3><div class='meta'><span class='chip'>"+escapeHtml(item.variable)+"</span></div><p>"+escapeHtml(item.description)+"</p><button class='secondary'>Fetch external CSV</button>";
    el.querySelector("button").onclick=function(){downloadExternal(item,el.querySelector("button"));};root.appendChild(el);
  });
}

async function downloadExternal(item,button){
  try{
    validateInputs(false);button.disabled=true;button.textContent="Fetching…";
    const body={component:$("component").value.trim(),bbox:bbox(),date_range:dates(),grid_points_per_axis:3,output_name:$("outputName").value.trim()||null};
    const blob=await api("/api/download/external/"+item.id,body,true);
    let name=$("outputName").value.trim()||slug($("component").value)+"_"+item.id+".csv";if(!name.toLowerCase().endsWith(".csv")) name+=".csv";
    downloadBlob(blob,name);toast("CSV created from "+item.provider+".");
  }catch(e){toast(e.message,"error");}
  finally{button.disabled=false;button.textContent="Fetch external CSV";}
}
setDefaultDates();health();
