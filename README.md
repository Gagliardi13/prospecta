# Prospecta

Encontra empresas de um segmento (advocacia, odontologia, contabilidade...) numa cidade ou
em bairros específicos e monta uma planilha com nome, CNPJ, sócios, telefone, WhatsApp,
e-mail, site, Instagram, endereço e nota no Google.

Ele combina três fontes:

| Fonte | O que traz | Custo |
|---|---|---|
| **Base de CNPJ da Receita** (dados abertos) | todas as empresas com CNPJ, **sócios**, endereço, bairro, telefone e e-mail cadastrados | grátis |
| **Google Maps** (Places API) | telefone atualizado, **site**, nota, profissionais sem CNPJ | 1.000 chamadas grátis/mês |
| **Site da própria empresa** | **e-mails** e **WhatsApp** publicados, Instagram/Facebook/LinkedIn | grátis |

---

## Instalação (uma vez só)

**Windows:** abra o **PowerShell** (menu Iniciar → digite "PowerShell") e cole:

```powershell
irm https://raw.githubusercontent.com/Gagliardi13/prospecta/main/instalar.ps1 | iex
```

**Linux / macOS:** no terminal:

```bash
curl -LsSf https://raw.githubusercontent.com/Gagliardi13/prospecta/main/instalar.sh | bash
```

Leva alguns minutos na primeira vez. No fim aparece um atalho **Prospecta** na área de trabalho
(Windows) ou no menu de aplicativos (Linux), e a tela abre no navegador (http://localhost:8501).

## Atualizações

Automáticas: **toda vez que você abre pelo atalho**, ele baixa a versão mais nova do GitHub
(se houver) antes de abrir. Sem internet, abre a versão que já está instalada.

Seus dados (base da Receita, chave do Google, resultados, segmentos personalizados) ficam na
pasta **`Prospecta`** dentro da sua pasta de usuário e **não são apagados** pelas atualizações.
A chave do Google fica só no seu computador, nunca no GitHub.

Para fechar o programa, feche a janela preta do terminal.

## Primeiro uso: baixar a base da Receita

Na barra lateral, clique em **"⬇️ Baixar e preparar a base"**.

- Baixa alguns GB e converte para um formato compacto. Pode levar de 30 min a algumas horas,
  dependendo da internet. Dá para deixar rodando e voltar depois; se cair, ao clicar de novo
  ele **continua de onde parou**.
- Deixe marcado só o estado que você usa (ex.: **SP**) para gastar menos disco.
  Reserve uns **15 GB livres** durante a importação (depois fica bem menos).
- A Receita atualiza a base todo mês; repita quando quiser dados novos.
- Se o site da Receita mudar de endereço: baixe os `.zip` pelo navegador
  (procure "Dados Abertos CNPJ" no dados.gov.br), coloque em `Prospecta/receita/zips/manual` (na sua pasta de usuário)
  e clique em **"📂 Importar da pasta"**. Ou ajuste o endereço no campo da barra lateral.

## Chave do Google (opcional, recomendado)

Sem a chave, você ainda tem tudo o que vem da Receita. Com ela, ganha site, telefone mais
atual e os profissionais que só aparecem no Google.

1. Acesse https://console.cloud.google.com e crie um projeto (ex.: "prospecta").
2. Em **Faturamento**, vincule uma conta com cartão. O Google exige isso mesmo para o uso gratuito.
3. Em **APIs e serviços → Biblioteca**, procure **"Places API (New)"** e clique em **Ativar**.
4. Em **APIs e serviços → Credenciais**, clique em **Criar credenciais → Chave de API**.
   Depois, em "Restrições de API", deixe a chave liberada só para a *Places API (New)*.
5. Cole a chave na barra lateral do Prospecta.
6. *(Segurança extra)* Em **Faturamento → Orçamentos e alertas**, crie um alerta de R$ 10,
   e em **Cotas** da Places API limite as chamadas por dia.

**Custo:** como o programa pede telefone e site, cada chamada cai na faixa "Text Search
Enterprise" do Google: **1.000 chamadas grátis por mês**, depois cerca de US$ 35 a cada 1.000.
Cada chamada traz até 20 empresas. A opção **"Nunca passar da cota grátis"** (ligada por padrão)
faz o programa parar antes de passar de 1.000 no mês. O contador conta só as chamadas feitas por
este programa; confira o painel do Google se usar a chave em outro lugar.

## Como buscar

1. **Segmento:** escolha na lista ou use *"Outro segmento"* e procure a atividade pelo nome
   ou código CNAE (ex.: "odonto", "8630504").
2. **Local:** estado, cidade e, se quiser, bairros (vazio = cidade inteira). O número ao lado
   de cada bairro é quantas empresas a Receita tem ali.
3. **O que buscar:** marque as fontes e clique em **Buscar**.

O resultado aparece na tela e pode ser baixado em CSV:

- **CSV para Excel (;)**: abre direto no Excel com acentos certos;
- **CSV padrão (,)**: para importar em n8n, CRM, ferramentas de disparo etc.

Uma cópia de cada busca fica em `Prospecta/resultados/` (na sua pasta de usuário).

## Colunas da planilha

| Coluna | O que é |
|---|---|
| `origem` | Receita, Google ou os dois (cruzados) |
| `nome`, `razao_social`, `nome_google`, `cnpj` | identificação |
| `socios_responsaveis` | sócios e cargo (ex.: "ANA SILVA (Sócio-Administrador)") |
| `telefone_principal` | melhor telefone (Google > site > Receita) |
| `whatsapp` | número em formato internacional (+5511...) pronto para automação |
| `whatsapp_status` | **confirmado no site** (o escritório divulga) ou **provável (celular)** |
| `celulares`, `telefones_fixos` | todos os números encontrados |
| `email_principal`, `emails` | e-mails; os do domínio do site vêm primeiro |
| `site`, `site_origem` | site e de onde veio (Google ou domínio do e-mail) |
| `instagram`, `facebook`, `linkedin` | redes encontradas no site |
| `endereco`, `bairro`, `cep`, `cidade`, `uf` | localização |
| `data_abertura`, `porte` | da Receita |
| `nota_google`, `avaliacoes_google`, `link_maps` | do Google |
| `completude` | 0 a 5: quantos contatos úteis a empresa tem (comece pelos 5) |
| `observacoes` | avisos (ex.: e-mail parece ser do contador) |

## Criar um segmento fixo

Abra `Prospecta/meus_segmentos.yaml` (na sua pasta de usuário) num editor de texto e siga o
modelo que está dentro. Na próxima vez que abrir o programa, ele aparece na lista.
Os segmentos que já vêm prontos ficam em `src/prospecta/segmentos.yaml`.

## Limitações e cuidados

- **Telefone e e-mail da Receita** muitas vezes são do contador ou estão desatualizados. O
  programa avisa quando o e-mail parece de contabilidade.
- **WhatsApp "provável"** quer dizer que o número é celular. O programa não testa número por
  número no WhatsApp, porque isso só é possível com ferramentas não oficiais que violam os
  termos e podem banir o seu número.
- **Profissional sem CNPJ** (advogado pessoa física) só aparece se tiver perfil no Google.
- **Disparos:**
  - *WhatsApp:* mensagem em massa para quem não pediu contato viola a política do WhatsApp e
    leva a bloqueio. O caminho oficial é a API do WhatsApp Business, com modelos aprovados e
    consentimento de quem recebe.
  - *E-mail:* para e-mail frio B2B, identifique-se, seja relevante para a empresa e ofereça
    descadastro.
  - *Dados dos sócios:* nome de sócio é dado pessoal (LGPD). Use para contato profissional,
    registre o legítimo interesse e atenda quem pedir para sair da lista.
- O programa lê só páginas públicas dos sites (início e contato), respeita o `robots.txt`
  e não faz scraping do Google Maps.

## Estrutura do código

```
instalar.ps1 / instalar.sh   instaladores (criam o atalho que se atualiza sozinho)
src/prospecta/app.py         tela (Streamlit)
src/prospecta/pipeline.py    junta as fontes e monta a planilha
src/prospecta/receita.py     download/importação/consulta da base da Receita
src/prospecta/google_places.py  Google Places API (New), cache de 30 dias
src/prospecta/site_scan.py   leitura dos sites (e-mail, WhatsApp, redes)
src/prospecta/util.py        normalização de nomes, bairros e telefones
src/prospecta/segmentos.yaml segmentos prontos
```

Para rodar a partir do código (desenvolvimento): `uv run prospecta`
