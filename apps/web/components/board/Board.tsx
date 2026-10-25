"use client";

import React from "react";
import { Column } from "./Column";

const DEFAULT_COLUMNS = [
  { id: "todo", title: "待办", count: 0 },
  { id: "in-progress", title: "进行中", count: 0 },
  { id: "done", title: "已完成", count: 0 },
];

export function Board() {
  return (
    <div className="h-screen w-full overflow-x-auto">
      <div className="flex gap-4 p-6 h-full items-start">
        {DEFAULT_COLUMNS.map((col) => (
          <Column key={col.id} title={col.title} count={col.count} />
        ))}
      </div>
    </div>
  );
}
