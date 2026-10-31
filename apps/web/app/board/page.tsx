"use client";

import Link from "next/link";
import { ChevronLeft } from "lucide-react";

import { Board } from "@/components/board/Board";

/** Legacy kanban kept at /board so the desktop shell and old links still work. */
export default function BoardPage() {
  return (
    <div className="board-legacy">
      <header className="board-legacy__bar">
        <Link href="/me" className="board-legacy__back">
          <ChevronLeft size={18} strokeWidth={1.5} aria-hidden="true" />
          返回生活助手
        </Link>
        <h1 className="board-legacy__title">旧版看板</h1>
      </header>
      <div className="board-legacy__body">
        <Board />
      </div>
    </div>
  );
}
