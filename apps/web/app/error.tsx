"use client";

import Link from "next/link";

/** Offer a safe exit when a page fails to render. */
export default function PageError() {
  return <main className="m-app auth-app">
    <h1>页面出错了</h1>
    <Link href="/today" className="btn btn--primary">回到首页</Link>
  </main>;
}
