import { RouterProvider } from "react-router";
import { router } from "./routes";
import { AtlasDataProvider } from "./data/AtlasDataProvider";

export default function App() {
  return (
    <AtlasDataProvider>
      <RouterProvider router={router} />
    </AtlasDataProvider>
  );
}
