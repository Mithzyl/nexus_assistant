import Link from "next/link";

/** Send users back to Today when a Web route is unavailable. */
export default function NotFound() {
  return <main className="m-app auth-app">
    <h1>找不到这个页面</h1>
    <p>它可能已经被删掉了。</p>
    <Link href="/today" className="btn btn--primary">回到首页</Link>
  </main>;
}
