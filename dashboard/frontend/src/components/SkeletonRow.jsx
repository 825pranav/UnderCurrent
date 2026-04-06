import React from "react";

export default function SkeletonRow({ cols = 7 }) {
  return (
    <tr>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-6 py-4">
          <div className="skeleton h-4 rounded-full" style={{ maxWidth: i === cols - 1 ? "70%" : "100%" }} />
        </td>
      ))}
    </tr>
  );
}
