import React, { useEffect, useState } from 'react';

type Api = (path:string, method?:string, body?:unknown)=>Promise<any>;
type Item = {
  id:number;title:string;prompt:string;cwd:string;status:string;source:string;
  model:string;effort:string;actual_model:string;actual_effort:string;
  message:string;thread_id:string;turn_id:string;started_at:string;
  finished_at:string;created_at:string;resume_count:number;retry_count:number;
};
const localTime=(value:string)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
const sourceLabel:Record<string,string>={queue:'新建对话',followup:'接续指定对话',auto:'自动发现',existing:'手动监控'};

export function QueueHistory({api}:{api:Api}){
  const [items,setItems]=useState<Item[]>([]);
  const [loaded,setLoaded]=useState(false);
  const [error,setError]=useState('');
  const [busyId,setBusyId]=useState<number|null>(null);

  useEffect(()=>{
    let live=true;
    const load=()=>api('/night-queue').then(value=>{
      if(!live)return;
      setItems((value.items||[]).filter((item:Item)=>['completed','failed'].includes(item.status))
        .sort((a:Item,b:Item)=>(b.finished_at||b.started_at||'').localeCompare(a.finished_at||a.started_at||'')||b.id-a.id));
      setLoaded(true);
      setError('');
    }).catch(reason=>{if(live){setError(String(reason));setLoaded(true)}});
    load();
    const timer=setInterval(load,10000);
    return()=>{live=false;clearInterval(timer)};
  },[api]);

  async function remove(id:number){
    setBusyId(id);
    setError('');
    try{
      await api(`/night-queue/items/${id}`,'DELETE');
      setItems(current=>current.filter(item=>item.id!==id));
    }catch(reason){setError(String(reason))}
    finally{setBusyId(null)}
  }

  return <div className="queue-history">
    <section className="panel"><div className="panel-heading"><div><h2>运行历史</h2><p className="muted">已完成与已停止的任务按结束时间从近到远排列。点击标题查看任务指令和运行详情。</p></div><span>{items.length} 项</span></div>{error&&<p className="error" role="alert">{error}</p>}</section>
    {!loaded?<div className="loading" role="status">正在读取运行历史…</div>:items.length?<section className="panel"><div className="queue-history-list">{items.map(item=><details key={item.id} className="queue-history-item"><summary><span translate="no">{item.title}</span><time className="queue-history-created" dateTime={item.created_at||undefined}>创建 {localTime(item.created_at)}</time></summary><div className="queue-history-body"><div className="facts"><span>状态 <b>{item.status==='completed'?'已完成':'失败'}</b></span><span>类型 <b>{sourceLabel[item.source]||item.source||'未知'}</b></span><span>模型 <b>{item.actual_model||item.model||'未知'}</b></span><span>思考深度 <b>{item.actual_effort||item.effort||'未知'}</b></span></div><p>工作目录：<code>{item.cwd||'未知'}</code></p>{item.message&&<p>{item.message}</p>}<p>创建 {localTime(item.created_at)} · 开始 {localTime(item.started_at)} · 结束 {localTime(item.finished_at)}</p>{item.resume_count>0&&<p>自动续接尝试 {item.resume_count} 次（含未成功的尝试）</p>}{item.retry_count>0&&<p>重试 {item.retry_count} 次</p>}{item.thread_id&&<p>Codex 对话 ID：<code>{item.thread_id}</code></p>}{item.turn_id&&<p>轮次 ID：<code>{item.turn_id}</code></p>}{item.prompt&&<><h3>完整任务指令</h3><pre>{item.prompt}</pre></>}<button type="button" className="danger" disabled={busyId!==null} onClick={()=>remove(item.id)}>删除记录</button></div></details>)}</div></section>:<section className="panel"><p className="muted">暂无历史任务。</p></section>}
  </div>;
}
