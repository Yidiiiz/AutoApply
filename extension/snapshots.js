// Background-owned, event-driven diagnostics. Session digests survive MV3 restarts.
export function snapshotCollector({api, current, report, delay}) {
  let queue=Promise.resolve();
  async function preference(value) {
    if(value!==undefined)await chrome.storage.local.set({autoSendStructure:Boolean(value)});
    const {autoSendStructure=true}=await chrome.storage.local.get('autoSendStructure');
    return {enabled:autoSendStructure!==false};
  }
  const unresolved=result=>[...(result.pending || []), ...(result.writingSettled && result.summary?.errors.length ? result.writing || [] : [])];
  function eligible(result,problem) {
    return current(result) && !result.stalePage && !result.busy && !result.skillsBusy && !result.coverBusy && !result.waiting && !result.sign_in && !result.error &&
      (!result.writing?.length || result.writingSettled) && JSON.stringify(unresolved(result))===problem;
  }
  async function collect(result) {
    const fields=unresolved(result), problem=JSON.stringify(fields);
    if(!fields.length || !eligible(result,problem) || !(await preference()).enabled)return;
    async function structure() {
      if(!eligible(result,problem) || (await chrome.tabs.get(result.tabId)).url!==result.url)return;
      const nodes=[];let truncated=false,bytes=0;
      for(const frameId of new Set([0,...fields.map(field=>Number(field.key.split(':')[0]))])) {
        const identity=result.pageIdentities?.[frameId];if(!identity)return;
        const local=fields.filter(field=>field.key.startsWith(frameId+':')).map(field=>({key:field.key.slice(field.key.indexOf(':')+1),label:field.label}));
        // Passive reads preserve the scanner's live handles, including during manual Send.
        const [frame]=await chrome.scripting.executeScript({target:{tabId:result.tabId,frameIds:[frameId]},
          func:(identity,fields)=>globalThis.autoApplyContent?.unresolvedStructure(identity,fields),args:[identity.identity,local]});
        if(!frame?.result || frame.documentId!==identity.documentId)return;
        truncated ||= frame.result.truncated;
        for(const node of frame.result.nodes) {
          bytes+=new TextEncoder().encode(JSON.stringify(node)).length;
          if(nodes.length>=10000 || bytes>900000){truncated=true;break;}
          nodes.push(node);
        }
      }
      return {host:new URL(result.url).hostname,nodes,truncated};
    }
    const snapshot=await structure();if(!snapshot || !eligible(result,problem))return;
    const problems=fields.map(field=>({label:field.label,kind:field.kind,options:field.options || []}))
      .sort((a,b)=>JSON.stringify(a).localeCompare(JSON.stringify(b)));
    const diagnostic={reason:'unresolved_fields',fields:problems.slice(0,50).map(field=>({label:field.label.slice(0,300),kind:field.kind,
      options:field.options.slice(0,20).map(option=>option.slice(0,200))}))};
    // Reserve room for diagnostic metadata, including multibyte choice labels.
    const encoder=new TextEncoder(), budget=980000-encoder.encode(JSON.stringify(diagnostic)).length;
    let size=0;
    const nodes=snapshot.nodes.filter(node=>{size+=encoder.encode(JSON.stringify(node)).length+1;return size<=budget;});
    const payload={...snapshot,nodes,truncated:snapshot.truncated || nodes.length!==snapshot.nodes.length,diagnostic};
    const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify([result.url,result.workday_step,result.pageIdentities?.[0]?.identity,snapshot,problems])));
    const signature=result.tabId+':'+Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
    const {structureCaptures=[]}=await chrome.storage.session.get('structureCaptures');
    let entry=structureCaptures.find(item=>item.signature===signature);
    if(!entry){entry={signature,attempts:0,sent:false};structureCaptures.push(entry);}
    const attempted=!entry.sent && entry.attempts<2;
    while(!entry.sent && entry.attempts<2) {
      if(!eligible(result,problem) || !(await preference()).enabled || !await structure())return;
      // Reserve each attempt before sending so a worker restart cannot cause a storm.
      entry.attempts++;
      await chrome.storage.session.set({structureCaptures:structureCaptures.slice(-100)});
      if(!eligible(result,problem) || !(await preference()).enabled || (await chrome.tabs.get(result.tabId)).url!==result.url)return;
      const latest=await structure();
      if(!latest || JSON.stringify(latest)!==JSON.stringify(snapshot) || !(await preference()).enabled || !eligible(result,problem))return;
      try {
        const response=await api('/ext/snapshot',payload);
        if(!response.ok)throw Error('Snapshot was not saved');
        entry.sent=true;
        await chrome.storage.session.set({structureCaptures:structureCaptures.slice(-100)});
      } catch {if(entry.attempts<2)await delay(500);}
    }
    if(eligible(result,problem)) {
      if(attempted){entry.feedback={id:crypto.randomUUID(),sent:entry.sent};await chrome.storage.session.set({structureCaptures:structureCaptures.slice(-100)});}
      result.snapshotFeedback=entry.feedback;
      await report(result);
    }
  }
  return {preference, capture(result) {
    queue=queue.catch(()=>{}).then(()=>collect(result)).catch(()=>{
      if(current(result)){result.snapshotFeedback={id:crypto.randomUUID(),sent:false};return report(result);}
    });
    return queue;
  }};
}
