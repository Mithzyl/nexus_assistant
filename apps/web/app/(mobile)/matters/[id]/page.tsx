import { MatterRealDetail } from "@/components/mobile/matter-real-detail";

export default function MatterDetailPage({ params }: { params: { id: string } }) {
  return <MatterRealDetail id={params.id} />;
}
