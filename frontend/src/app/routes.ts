import { createBrowserRouter } from "react-router";
import { Layout } from "./components/Layout";
import { Landing } from "./pages/Landing";
import { CommunityMap } from "./pages/CommunityMap";
import { ChannelDetail } from "./pages/ChannelDetail";
import { Stats } from "./pages/Stats";
import { About } from "./pages/About";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: Layout,
    children: [
      { index: true, Component: Landing },
      { path: "map", Component: CommunityMap },
      { path: "channel/:id", Component: ChannelDetail },
      { path: "stats", Component: Stats },
      { path: "about", Component: About },
    ],
  },
]);