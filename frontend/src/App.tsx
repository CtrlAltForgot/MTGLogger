import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { AutoStories, CollectionsBookmark, DarkMode, Download, Inventory2, LightMode, MenuBook, Paid, Search, Visibility } from '@mui/icons-material'
import { AppBar, Box, Button, CircularProgress, CssBaseline, IconButton, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography } from '@mui/material'
import { appTheme } from './theme'
import { CardDetailsProvider } from './components/CardDetails'

const Collection=lazy(()=>import('./pages/Collection'))
const Value=lazy(()=>import('./pages/Value'))
const Dashboard=lazy(()=>import('./pages/Dashboard'))
const Decks=lazy(()=>import('./pages/Decks'))
const ReviewQueue=lazy(()=>import('./pages/ReviewQueue'))
const Scanner=lazy(()=>import('./pages/Scanner'))
const Database=lazy(()=>import('./pages/Database'))

const pages=[
  {name:'Dashboard',icon:<MenuBook/>,content:<Dashboard/>},
  {name:'Scanner',icon:<Visibility/>,content:<Scanner/>},
  {name:'Collection',icon:<CollectionsBookmark/>,content:<Collection/>},
  {name:'Value',icon:<Paid/>,content:<Value/>},
  {name:'Database',icon:<AutoStories/>,content:<Database/>},
  {name:'Decks',icon:<Inventory2/>,content:<Decks/>},
  {name:'Review',icon:<Search/>,content:<ReviewQueue/>},
]

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{outcome:'accepted'|'dismissed'}>
}

export default function App(){
  const requestedPage=new URLSearchParams(location.search).get('page')
  const requestedIndex=pages.findIndex(item=>item.name.toLowerCase()===requestedPage)
  const [page,setPage]=useState(requestedIndex>=0?requestedIndex:1)
  const [dark,setDark]=useState(()=>localStorage.getItem('mtglogger-theme')!=='light')
  const [installPrompt,setInstallPrompt]=useState<InstallPromptEvent>()
  const theme=useMemo(()=>appTheme(dark),[dark])
  useEffect(()=>{
    const capture=(event:Event)=>{event.preventDefault();setInstallPrompt(event as InstallPromptEvent)}
    const installed=()=>setInstallPrompt(undefined)
    window.addEventListener('beforeinstallprompt',capture)
    window.addEventListener('appinstalled',installed)
    return ()=>{window.removeEventListener('beforeinstallprompt',capture);window.removeEventListener('appinstalled',installed)}
  },[])
  const toggleTheme=()=>setDark(value=>{localStorage.setItem('mtglogger-theme',value?'light':'dark');return !value})
  const changePage=(value:number)=>{setPage(value);history.replaceState(null,'',`?page=${pages[value].name.toLowerCase()}`)}
  const install=async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;setInstallPrompt(undefined)}
  return <ThemeProvider theme={theme}><CssBaseline/><CardDetailsProvider>
    <AppBar position="sticky"><Toolbar sx={{minHeight:{xs:54,md:64},px:{xs:1.25,md:2},gap:{xs:.75,md:1.5}}}>
      <Box component="img" src="/mtglogger-card-stack.png" alt="" sx={{width:{xs:34,md:40},height:{xs:34,md:40},objectFit:'contain',flex:'0 0 auto',filter:'drop-shadow(0 8px 18px rgba(190,35,54,.3))'}}/>
      <Box sx={{flex:'0 0 auto',display:{xs:'none',lg:'block'},minWidth:150}}><Typography variant="h6" lineHeight={1}>MTGLogger</Typography><Typography variant="caption" color="text.secondary">Log your TCG collection</Typography></Box>
      <Tabs value={page} onChange={(_,value)=>changePage(value)} variant="scrollable" scrollButtons={false} aria-label="Main navigation" sx={{flex:1,minWidth:0,maxWidth:{md:900},mx:'auto','& .MuiTab-root':{minWidth:0,minHeight:{xs:54,md:64},fontSize:{xs:'.78rem',md:'.95rem'},px:{xs:1,md:1.65},gap:{xs:.35,md:.7}},'& .MuiSvgIcon-root':{fontSize:{xs:21,md:25}}}}>{pages.map(item=><Tab key={item.name} icon={item.icon} iconPosition="start" label={item.name}/>)}</Tabs>
      {installPrompt&&<Button startIcon={<Download/>} onClick={()=>void install()} sx={{display:{xs:'none',xl:'inline-flex'},flex:'0 0 auto'}}>Install</Button>}
      <Tooltip title={dark?'Use light appearance':'Use dark appearance'}><IconButton aria-label={dark?'Use light appearance':'Use dark appearance'} onClick={toggleTheme} sx={{flex:'0 0 auto',border:'1px solid',borderColor:'divider',bgcolor:'action.hover'}}>{dark?<LightMode/>:<DarkMode/>}</IconButton></Tooltip>
    </Toolbar></AppBar>
    <Box component="main" sx={{maxWidth:1580,mx:'auto',px:{xs:2,sm:3,lg:3.5},py:{xs:2.5,md:3},minHeight:'calc(100vh - 64px)'}}><Suspense fallback={<Box minHeight="50vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>}>{pages[page].content}</Suspense></Box>
  </CardDetailsProvider></ThemeProvider>
}
