import { MatterDetail } from "@/components/mobile/matter-detail";
import { WorkMatterDetail } from "@/components/mobile/work-matter-detail";

export default function MatterDetailPage({ params }: { params: { id: string } }) {
  if (params.id.startsWith("wp-")) return <WorkMatterDetail id={params.id} />;
  return <MatterDetail id={params.id} />;
}
