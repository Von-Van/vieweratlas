import { createBrowserRouter } from "react-router";
import { Layout } from "./components/Layout";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      {
        index: true,
        lazy: async () => ({ Component: (await import("./pages/Landing")).Landing }),
      },
      {
        path: "map",
        lazy: async () => ({ Component: (await import("./pages/CommunityMap")).CommunityMap }),
      },
      {
        path: "channel/:id",
        lazy: async () => ({ Component: (await import("./pages/ChannelDetail")).ChannelDetail }),
      },
      {
        path: "stats",
        lazy: async () => ({ Component: (await import("./pages/Stats")).Stats }),
      },
      {
        path: "about",
        lazy: async () => ({ Component: (await import("./pages/About")).About }),
      },
    ],
  },
]);
