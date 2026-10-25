import { redirect } from "next/navigation";

/** The mobile client starts on 今天; `/board` remains the legacy kanban. */
export default function IndexPage() {
  redirect("/today");
}
