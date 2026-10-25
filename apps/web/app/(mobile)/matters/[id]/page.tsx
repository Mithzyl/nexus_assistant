import { MatterDetail } from "@/components/mobile/matter-detail";

export default function MatterDetailPage({ params }: { params: { id: string } }) {
  return <MatterDetail id={params.id} />;
}
