import React, { useEffect, useRef, useState } from 'react';
export function InfoTip({title,children}:{title:string;children:React.ReactNode}){
  const [open,setOpen]=useState(false);const root=useRef<HTMLSpanElement>(null);const id=React.useId();
  useEffect(()=>{if(!open)return;const close=(e:MouseEvent)=>{if(!root.current?.contains(e.target as Node))setOpen(false)};const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);root.current?.querySelector('button')?.focus()}};document.addEventListener('mousedown',close);document.addEventListener('keydown',key);return()=>{document.removeEventListener('mousedown',close);document.removeEventListener('keydown',key)}},[open]);
  return <span className="info-tip" ref={root}><button type="button" aria-label={`解释：${title}`} aria-expanded={open} aria-controls={id} onClick={e=>{e.preventDefault();e.stopPropagation();setOpen(!open)}}>?</button>{open&&<span id={id} className="info-popover" role="note" onClick={e=>e.stopPropagation()}><strong>{title}</strong><span>{children}</span></span>}</span>
}
