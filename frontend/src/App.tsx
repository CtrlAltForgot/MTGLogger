import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { AutoStories, DarkMode, Download, Home, Inventory2, LightMode, MoreHoriz, Paid, PhoneIphone, Search, SportsEsports, Style, Visibility } from '@mui/icons-material'
import { AppBar, BottomNavigation, BottomNavigationAction, Box, Button, CircularProgress, CssBaseline, IconButton, ListItemIcon, ListItemText, Menu, MenuItem, Paper, Tab, Tabs, ThemeProvider, Toolbar, Tooltip, Typography } from '@mui/material'
import { appTheme } from './theme'
import { CardDetailsProvider } from './components/CardDetails'

const Collection=lazy(()=>import('./pages/Collection'))
const Value=lazy(()=>import('./pages/Value'))
const Dashboard=lazy(()=>import('./pages/Dashboard'))
const Decks=lazy(()=>import('./pages/Decks'))
const ReviewQueue=lazy(()=>import('./pages/ReviewQueue'))
const Scanner=lazy(()=>import('./pages/Scanner'))
const Database=lazy(()=>import('./pages/Database'))
const Play=lazy(()=>import('./pages/Play'))

const pages=[
  {name:'Dashboard',icon:<Home/>,content:<Dashboard/>},
  {name:'Scanner',icon:<Visibility/>,content:<Scanner/>},
  {name:'Collection',icon:<Style/>,content:<Collection/>},
  {name:'Value',icon:<Paid/>,content:<Value/>},
  {name:'Database',icon:<AutoStories/>,content:<Database/>},
  {name:'Decks',icon:<Inventory2/>,content:<Decks/>},
  {name:'Play',icon:<SportsEsports/>,content:<Play/>},
  {name:'Review',icon:<Search/>,content:<ReviewQueue/>},
]

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
  userChoice: Promise<{outcome:'accepted'|'dismissed'}>
}

export default function App(){
  const native=new URLSearchParams(location.search).get('native')==='1'
  const requestedPage=new URLSearchParams(location.search).get('page')
  const requestedIndex=pages.findIndex(item=>item.name.toLowerCase()===requestedPage)
  const [page,setPage]=useState(requestedIndex>=0?requestedIndex:1)
  const [dark,setDark]=useState(()=>localStorage.getItem('mtglogger-theme')!=='light')
  const [installPrompt,setInstallPrompt]=useState<InstallPromptEvent>()
  const [moreAnchor,setMoreAnchor]=useState<HTMLElement|null>(null)
  const theme=useMemo(()=>appTheme(dark),[dark])
  useEffect(()=>{
    const capture=(event:Event)=>{event.preventDefault();setInstallPrompt(event as InstallPromptEvent)}
    const installed=()=>setInstallPrompt(undefined)
    window.addEventListener('beforeinstallprompt',capture)
    window.addEventListener('appinstalled',installed)
    return ()=>{window.removeEventListener('beforeinstallprompt',capture);window.removeEventListener('appinstalled',installed)}
  },[])
  const toggleTheme=()=>setDark(value=>{localStorage.setItem('mtglogger-theme',value?'light':'dark');return !value})
  const changePage=(value:number)=>{setPage(value);setMoreAnchor(null);history.replaceState(null,'',`?page=${pages[value].name.toLowerCase()}${native?'&native=1':''}`);window.scrollTo({top:0})}
  const install=async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;setInstallPrompt(undefined)}
  return <ThemeProvider theme={theme}><CssBaseline/><CardDetailsProvider><Box className={`app-frame ${page===6?'is-play':''} ${native?'is-native':''}`}>
    <AppBar position="sticky"><Toolbar sx={{position:'relative',minHeight:{xs:54,md:64},px:{xs:1.25,md:2},gap:{xs:.75,md:1.5}}}>
      <Box component="img" src="/mtglogger-card-stack.png" alt="" sx={{width:{xs:34,md:40},height:{xs:34,md:40},objectFit:'contain',flex:'0 0 auto',filter:'drop-shadow(0 8px 18px rgba(190,35,54,.3))'}}/>
      <Box sx={{flex:{xs:1,md:'0 0 auto'},display:{xs:'block',md:'none',xl:'block'}}}><Typography variant="h6" lineHeight={1}>MTGLogger</Typography><Typography variant="caption" color="text.secondary" sx={{display:{xl:'none'}}}>{pages[page].name}</Typography></Box>
      <Tabs value={page} onChange={(_,value)=>changePage(value)} variant="scrollable" scrollButtons="auto" aria-label="Main navigation" sx={{display:{xs:'none',md:'flex'},flex:1,minWidth:0,mx:'auto','& .MuiTabs-flexContainer':{justifyContent:{lg:'center'}},'& .MuiTab-root':{minWidth:0,minHeight:60,fontSize:'.88rem',px:{md:1.15,xl:1.6},gap:.6},'& .MuiSvgIcon-root':{fontSize:22}}}>{pages.map(item=><Tab key={item.name} icon={item.icon} iconPosition="start" label={item.name}/>)}</Tabs>
      <Tooltip title="Get the iPhone app"><IconButton component="a" href="/iphone.html" aria-label="Get the iPhone app"><PhoneIphone/></IconButton></Tooltip>
      {native&&<IconButton aria-label="All website pages" onClick={event=>setMoreAnchor(event.currentTarget)}><MoreHoriz/></IconButton>}
      {installPrompt&&<Button startIcon={<Download/>} onClick={()=>void install()} sx={{display:{xs:'none',xl:'inline-flex'},flex:'0 0 auto'}}>Install</Button>}
      <Tooltip title={dark?'Use light appearance':'Use dark appearance'}><IconButton aria-label={dark?'Use light appearance':'Use dark appearance'} onClick={toggleTheme} sx={{ml:{lg:'auto'},flex:'0 0 auto',border:'1px solid',borderColor:'divider',bgcolor:'action.hover'}}>{dark?<LightMode/>:<DarkMode/>}</IconButton></Tooltip>
    </Toolbar></AppBar>
    <Box component="main" sx={{maxWidth:page===6?'none':1580,mx:'auto',px:page===6?0:{xs:1.5,sm:2.5,lg:3.5},py:page===6?0:{xs:1.5,md:2.5},minHeight:page===6?0:'calc(100dvh - 64px)',flex:page===6?'1 1 auto':undefined,width:'100%'}}><Suspense fallback={<Box minHeight="50vh" display="grid" sx={{placeItems:'center'}}><CircularProgress/></Box>}>{pages[page].content}</Suspense></Box>
    <Paper component="nav" aria-label="Mobile navigation" square className="mobile-navigation" sx={{display:{xs:native?'none':'block',md:'none'},position:'fixed',bottom:0,left:0,right:0,zIndex:1100,borderTop:'1px solid',borderColor:'divider',pb:'env(safe-area-inset-bottom)'}}>
      <BottomNavigation showLabels value={[1,2,7].includes(page)?page:'more'} onChange={(event,value)=>value==='more'?setMoreAnchor(event.currentTarget as HTMLElement):changePage(value)} sx={{bgcolor:'background.paper','& .MuiBottomNavigationAction-root':{minWidth:0,px:1}}}>
        <BottomNavigationAction label="Scan" value={1} icon={<Visibility/>}/><BottomNavigationAction label="Collection" value={2} icon={<Style/>}/><BottomNavigationAction label="Review" value={7} icon={<Search/>}/><BottomNavigationAction label="More" value="more" icon={<MoreHoriz/>} aria-haspopup="menu" aria-expanded={!!moreAnchor}/>
      </BottomNavigation>
    </Paper>
    <Menu anchorEl={moreAnchor} open={!!moreAnchor} onClose={()=>setMoreAnchor(null)} anchorOrigin={{vertical:native?'bottom':'top',horizontal:'right'}} transformOrigin={{vertical:native?'top':'bottom',horizontal:'right'}}>{pages.map((item,index)=>(native||![1,2,7].includes(index))&&<MenuItem key={item.name} selected={page===index} onClick={()=>changePage(index)}><ListItemIcon>{item.icon}</ListItemIcon><ListItemText>{item.name}</ListItemText></MenuItem>)}</Menu>
  </Box></CardDetailsProvider></ThemeProvider>
}
