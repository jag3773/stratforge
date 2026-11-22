export async function loadJson<T>(href: string): Promise<T> {
  const base = import.meta.env.BASE_URL || "/";
  const path = href.replace(/^\//, "");
  const url = base.endsWith("/") ? base + path : `${base}/${path}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`${url} ${res.status}`);
  }
  return (await res.json()) as T;
}
