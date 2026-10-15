import React, { useEffect, useState } from 'react';

type Api = (path:string, method?:string, body?:unknown)=>Promise<any>;
type Prompt = {id:string;turn_id:string|null;at:string|null;text:string;attachments:number;characters:number;source:string};
type Task = {id:string;title:string;prompt_count:number;character_count:number;last_at:string;prompts:Prompt[]};
type Project = {path:string;name:string;codex_project:string;task_count:number;prompt_count:number;character_count:number;tasks:Task[]};
type Snapshot = {disabled:boolean;projects:Project[];total_tasks:number;total_prompts:number;total_characters:number};
const number = (value:number)=>new Intl.NumberFormat('zh-CN').format(value||0);
const localTime = (value:string|null)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'时间未知';

export function PromptHistory({api}:{api:Api}){
  const [data,setData]=useState<Snapshot|null>(null);
  const [projectPath,setProjectPath]=useState('');
  const [taskId,setTaskId]=useState('');
  const [search,setSearch]=useState('');
  const [error,setError]=useState('');
  useEffect(()=>{let live=true;const load=()=>api('/prompts').then(value=>{if(live){setData(value);setError('')}}).catch(e=>{if(live)setError(String(e))});load();const timer=setInterval(load,15000);return()=>{live=false;clearInterval(timer)}},[api]);
  const projects=data?.projects||[];
  const project=projects.find(item=>item.path===projectPath)||projects[0];
  const tasks=project?.tasks||[];
  const query=search.trim().toLocaleLowerCase();
  const visibleTasks=query?tasks.filter(item=>item.title.toLocaleLowerCase().includes(query)||item.id.includes(query)||item.prompts.some(prompt=>prompt.text.toLocaleLowerCase().includes(query))):tasks;
  const task=visibleTasks.find(item=>item.id===taskId)||visibleTasks[0];
  return <div className="prompt-history">
    <section className="panel"><div className="panel-heading"><div><h2>我的 Prompt</h2><p className="muted">按项目和任务查看你发给 Codex 的输入。只读取本机用户主任务与已派发队列任务，不展示 Codex 回复。</p></div></div>
      {data?.disabled?<p className="notice">当前开启隐私模式，Prompt 原文不会在此页展示。</p>:<div className="facts"><span>项目 <b>{number(projects.length)}</b></span><span>任务 <b>{number(data?.total_tasks||0)}</b></span><span>Prompt <b>{number(data?.total_prompts||0)}</b></span><span>文本字符 <b>{number(data?.total_characters||0)}</b></span></div>}
      {error&&<p className="error" role="alert">读取 Prompt 失败：{error}</p>}
    </section>
    {!data?<div className="loading" role="status">正在读取本机 Prompt…</div>:!data.disabled&&projects.length===0?<section className="panel empty">暂无可读取的用户 Prompt。</section>:!data.disabled&&<div className="prompt-layout">
      <section className="panel prompt-projects"><h2>项目</h2><div className="prompt-picker">{projects.map(item=><button type="button" key={item.path} className={item.path===project?.path?'selected':''} aria-pressed={item.path===project?.path} onClick={()=>{setProjectPath(item.path);setTaskId('');setSearch('')}}><strong>{item.codex_project||item.name}</strong><small>{item.path}</small><span>{number(item.task_count)} 个任务 · {number(item.prompt_count)} 条 Prompt</span></button>)}</div></section>
      <section className="panel prompt-tasks"><h2>任务</h2><label>查找任务或 Prompt<input value={search} onChange={e=>{setSearch(e.target.value);setTaskId('')}} placeholder="标题、任务 ID、Prompt 内容"/></label><div className="prompt-picker">{visibleTasks.map(item=><button type="button" key={item.id} className={item.id===task?.id?'selected':''} aria-pressed={item.id===task?.id} onClick={()=>setTaskId(item.id)}><strong>{item.title}</strong><small>{item.id}</small><span>{number(item.prompt_count)} 条 · {number(item.character_count)} 字符</span></button>)}{!visibleTasks.length&&<p className="muted">没有匹配的任务。</p>}</div></section>
      <section className="panel prompt-conversation">{task?<><div className="panel-heading"><div><h2>{task.title}</h2><p className="muted">{project?.name} · {number(task.prompt_count)} 条 Prompt · {number(task.character_count)} 字符</p></div></div><div className="prompt-entries">{task.prompts.map((item,index)=><article key={`${item.id}-${index}`}><div className="prompt-entry-head"><span>#{index+1} · {localTime(item.at)}</span><span>{number(item.characters)} 字符{item.source==='queue'?' · 队列代发':''}{item.attachments?` · ${item.attachments} 个非文本附件`:''}</span></div>{item.text?<pre>{item.text}</pre>:<p className="muted">这条消息只有非文本附件，本地日志没有可展示的文字。</p>}</article>)}</div></>:<p className="empty">选择一个任务查看 Prompt。</p>}</section>
    </div>}
  </div>
}
