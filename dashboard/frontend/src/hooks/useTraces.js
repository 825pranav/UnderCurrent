import { useQuery } from "@tanstack/react-query";
import axios from "axios";

const REFETCH_MS = 4000;

const fetcher = (url) => axios.get(url).then((r) => r.data);

export function useTraces() {
  const traces = useQuery({
    queryKey: ["traces"],
    queryFn: () => fetcher("/api/traces"),
    refetchInterval: REFETCH_MS,
  });

  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: () => fetcher("/api/stats"),
    refetchInterval: REFETCH_MS,
  });

  const containers = useQuery({
    queryKey: ["containers"],
    queryFn: () => fetcher("/api/containers"),
    refetchInterval: REFETCH_MS,
  });

  const timeline = useQuery({
    queryKey: ["timeline"],
    queryFn: () => fetcher("/api/timeline"),
    refetchInterval: REFETCH_MS,
  });

  return {
    traces:     traces.data     ?? [],
    stats:      stats.data      ?? null,
    containers: containers.data ?? [],
    timeline:   timeline.data   ?? {},
    isLoading:  traces.isLoading || stats.isLoading || containers.isLoading || timeline.isLoading,
    isError:    traces.isError  || stats.isError  || containers.isError  || timeline.isError,
  };
}
