import { NgModule } from '@angular/core';
import { Routes, RouterModule } from '@angular/router';

import { Auth1Guard } from './helpers/auth1.guard';
import { Auth2Guard } from './helpers/auth2.guard';

import { SignInComponent } from './pages/sign-in/sign-in.component';
import { SignUpComponent } from './pages/sign-up/sign-up.component';
import { ForgotComponent } from './pages/forgot/forgot.component';
import { MainContentComponent } from './pages/main-content/main-content.component';
import { HomeComponent } from './pages/main-content/home/home.component';
import { ReportComponent } from './pages/main-content/report/report.component';
import { MemberComponent } from './pages/main-content/member/member.component';

const routes: Routes = [
  // { path: '', redirectTo: 'home', pathMatch: 'full' },
  { path: 'signin', component: SignInComponent },
  { path: 'signup', component: SignUpComponent },
  { path: 'forgot', component: ForgotComponent },
  {
    path: '', component: MainContentComponent,
    canActivate: [Auth1Guard],
    children: [
      { path: 'patient/not-accept', component: HomeComponent },
      { path: 'patient/accepted', component: HomeComponent },
      { path: 'patient/follow', component: HomeComponent },
      { path: 'report/:menuId', component: ReportComponent },
      { path: 'admin/member/list', component: MemberComponent },
      { path: '', redirectTo: 'patient/not-accept', pathMatch: 'full' }
    ]
  },
  {
    path: 'setting', component: MainContentComponent,
    canActivate: [Auth2Guard],
    children: [
      { path: 'member/list', component: MemberComponent }
    ]
  },
  { path: '**', redirectTo: 'signin', pathMatch: 'full' }
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule]
})
export class AppRoutingModule { }
