import { Link, useParams } from "react-router-dom";
import { labDownloadUrl, labViewUrl, studyDownloadUrl, studyViewUrl } from "../api/client";
import { PageHeader } from "../components/PageHeader";

export function DocumentViewerPage({ kind }: { kind: "lab" | "study" }) {
  const { id = "" } = useParams();
  const view = kind === "lab" ? labViewUrl(id) : studyViewUrl(id);
  const download = kind === "lab" ? labDownloadUrl(id) : studyDownloadUrl(id);
  const back = kind === "lab" ? `/labs/documents/${id}` : `/studies/${id}`;
  return <>
    <PageHeader eyebrow="Оригинал" title="Просмотр документа" description="Документ открыт напрямую из защищённого хранилища PostgreSQL." actions={<><Link className="button button--secondary" to={back}>Назад</Link><a className="button button--secondary" href={download}>Скачать</a></>} />
    <section className="panel document-viewer"><iframe src={view} title="Просмотр загруженного документа" /></section>
  </>;
}
