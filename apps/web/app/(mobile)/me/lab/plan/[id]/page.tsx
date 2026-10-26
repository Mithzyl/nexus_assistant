import { WorkMatterDetail } from "@/components/mobile/work-matter-detail";

export default function PreviewMatterDetailPage({ params }: { params: { id: string } }) {
  return <WorkMatterDetail id={params.id} />;
}
