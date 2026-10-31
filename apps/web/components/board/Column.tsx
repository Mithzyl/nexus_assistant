"use client";

import React from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Plus } from "lucide-react";

interface ColumnProps {
  title: string;
  count: number;
}

export function Column({ title, count }: ColumnProps) {
  return (
    <Card className="w-80 flex-shrink-0 flex flex-col">
      <CardHeader className="flex flex-row items-center justify-between pb-2">
        <CardTitle className="text-base font-medium">{title}</CardTitle>
        <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
          {count}
        </span>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col gap-2">
        <div className="flex-1 min-h-[160px] rounded-md border border-dashed border-muted-foreground/25 flex flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
          <span>暂无卡片</span>
          <Button variant="ghost" size="sm">
            <Plus className="h-4 w-4 mr-1" />
            添加卡片
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
