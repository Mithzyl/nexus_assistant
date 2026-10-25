// Shared types between web and desktop

export interface BoardData {
  id: string;
  name: string;
  columns: ColumnData[];
  createdAt: Date;
}

export interface ColumnData {
  id: string;
  name: string;
  order: number;
  boardId: string;
  cards: CardData[];
  createdAt: Date;
}

export interface CardData {
  id: string;
  title: string;
  content: string | null;
  columnId: string;
  parentId: string | null;
  labels: string;
  dueDate: Date | null;
  order: number;
  createdAt: Date;
  updatedAt: Date;
}
