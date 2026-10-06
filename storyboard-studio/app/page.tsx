import Studio from "@/components/Studio";

// 全部状态在客户端（一次生成就是一次长连接流），所以这里是薄壳。
export default function Page() {
  return <Studio />;
}
