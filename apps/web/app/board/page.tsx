"use client";

import { Board } from "@/components/board/Board";

/** Legacy kanban kept at /board so the desktop shell and old links still work. */
export default function BoardPage() {
  return (
    <div className="board-legacy">
      <Board />
    </div>
  );
}
