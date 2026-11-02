"use client";

import { useEffect, useState } from "react";
import { apiRequest, pa, type CommuteLocation, type PlaceCandidate, type PlaceSearchResponse } from "@/lib/mobile/api";

/** One place input; selecting a provider candidate commits its city and coordinates together. */
export function CommutePlaceInput({ id, title, value, onChange }: {
  id: string; title: string; value: CommuteLocation; onChange: (point: CommuteLocation) => void;
}) {
  const [rows, setRows] = useState<PlaceCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [composing, setComposing] = useState(false);
  const [retry, setRetry] = useState(0);
  const located = Number.isFinite(value.latitude) && Number.isFinite(value.longitude) &&
    (value.latitude !== 0 || value.longitude !== 0);

  useEffect(() => {
    let alive = true;
    setRows([]); setError(""); setBusy(false);
    const query = value.label.trim();
    if (composing || located || query.length < 2) return;
    setBusy(true);
    const timer = setTimeout(() => {
      apiRequest<PlaceSearchResponse>(pa(`/commute/places?query=${encodeURIComponent(query)}`))
        .then(result => {
          if (!alive) return;
          setRows(result.candidates);
          if (result.reason_code) setError("地点搜索暂时不可用，请稍后重试。");
          else if (!result.candidates.length) setError("没有找到匹配地点，请加上城市或更详细的地址。");
        })
        .catch(e => { if (alive) setError(e instanceof Error ? e.message : "地点搜索失败，请重试。"); })
        .finally(() => { if (alive) setBusy(false); });
    }, 600);
    return () => { alive = false; clearTimeout(timer); };
  }, [value.label, located, composing, retry]);

  return <div className="field">
    <label className="field__label" htmlFor={`${id}-place`}>搜索{title}</label>
    <input id={`${id}-place`} className="input" value={value.label} autoComplete="off"
      placeholder="输入地点名称或详细地址，建议带上城市"
      aria-describedby={`${id}-hint`} aria-controls={rows.length ? `${id}-results` : undefined}
      onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)}
      onChange={e => onChange({ label: e.target.value, latitude: 0, longitude: 0, city: null, coordinate_system: null })} />
    <p id={`${id}-hint`} className="field__hint" role="status">
      {located ? `已确认地点${value.city ? ` · ${value.city}` : ""}` : busy ? "正在查找地点…" : "输入后自动查找，请从下方候选中选择具体地点。"}
    </p>
    {rows.length > 0 && !located ? <ul id={`${id}-results`} aria-label={`${title}搜索结果`} style={{listStyle:"none",padding:0,margin:"4px 0"}}>
      {rows.map((p, index) => <li key={`${p.provider_place_id}-${index}`}>
        <button type="button" className="card card--tight" style={{width:"100%",textAlign:"left",marginBottom:6}}
          disabled={p.coordinate_system === "unknown"}
          onClick={() => onChange({label:p.name,city:p.city,latitude:p.latitude,longitude:p.longitude,coordinate_system:p.coordinate_system === "unknown" ? null : p.coordinate_system})}>
          <strong>{p.name}</strong><span className="caption" style={{display:"block",marginTop:4}}>{[p.city,p.address].filter(Boolean).join(" · ") || "地址未提供"}</span>
        </button>
      </li>)}
    </ul> : null}
    {error ? <p role="alert" className="caption red">{error} <button type="button" className="link-button" onClick={() => setRetry(n => n+1)}>重试搜索</button></p> : null}
    <details style={{marginTop:8}}><summary className="micro">高级：手动填写坐标</summary>
      <label className="field"><span className="field__label">城市</span><input className="input" value={value.city ?? ""} placeholder="例如：柳州市" onChange={e => onChange({...value,city:e.target.value || null})}/></label>
      <div className="flex gap-10">{(["latitude","longitude"] as const).map(k => <label key={k} className="field grow">
        <span className="field__label">{k === "latitude" ? "纬度" : "经度"}</span>
        <input className="input" type="number" step="0.000001" value={value[k] || ""} onChange={e => onChange({...value,[k]:Number(e.target.value),coordinate_system:value.coordinate_system ?? "gcj02"})}/>
      </label>)}</div>
      <label className="field"><span className="field__label">坐标系</span><select className="select" value={value.coordinate_system ?? "gcj02"} onChange={e => onChange({...value,coordinate_system:e.target.value as "gcj02" | "wgs84"})}><option value="gcj02">高德坐标（GCJ-02）</option><option value="wgs84">GPS 坐标（WGS84）</option></select></label>
    </details>
  </div>;
}
