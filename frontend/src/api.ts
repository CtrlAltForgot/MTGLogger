import type { Defaults } from './types'
export const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'
export async function request<T>(path:string, options?:RequestInit):Promise<T> { const response=await fetch(`${API}/api${path}`,options); if(!response.ok) throw new Error((await response.json().catch(()=>null))?.detail || `Request failed (${response.status})`); return response.status===204 ? undefined as T : response.json() }
export async function submitScan(blob:Blob, defaults:Defaults) { const body=new FormData(); body.append('image',blob,'capture.jpg'); body.append('defaults_json',JSON.stringify(defaults)); return request('/scanner/recognize',{method:'POST',body}) }

