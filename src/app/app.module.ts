import { BrowserModule } from '@angular/platform-browser';
import { NgModule } from '@angular/core';
import { FormsModule, ReactiveFormsModule } from '@angular/forms';
import { HttpClientModule, HTTP_INTERCEPTORS } from '@angular/common/http';
import { AngularMyDatePickerModule } from '@nodro7/angular-mydatepicker';
import { AppRoutingModule } from './app-routing.module';

import { JwtInterceptor } from './helpers/jwt.interceptor';
import { ErrorInterceptor } from './helpers/error.interceptor';
import { environment } from 'src/environments/environment';

import { AgePipe } from './pipes/age.pipe';
import { ThaiDatePipe } from './pipes/thaidate.pipe';
import { SumDataOnePipe } from './pipes/sumDataOne.pipe';
import { FilterPipe } from './pipes/filter.pipe';

import { AppComponent } from './app.component';
import { SignInComponent } from './pages/sign-in/sign-in.component';
import { MainContentComponent } from './pages/main-content/main-content.component';
import { HomeComponent } from './pages/main-content/home/home.component';
import { ReportComponent } from './pages/main-content/report/report.component';
import { ShDetails0Component } from './pages/sh/sh-details0/sh-details0.component';
import { ShDetails1Component } from './pages/sh/sh-details1/sh-details1.component';
import { ShDetails2Component } from './pages/sh/sh-details2/sh-details2.component';
import { ShDetails3Component } from './pages/sh/sh-details3/sh-details3.component';
import { SignUpComponent } from './pages/sign-up/sign-up.component';
import { ForgotComponent } from './pages/forgot/forgot.component';
import { MemberComponent } from './pages/main-content/member/member.component';

@NgModule({
  declarations: [
    AgePipe,
    ThaiDatePipe,
    SumDataOnePipe,
    FilterPipe,
    AppComponent,
    SignInComponent,
    MainContentComponent,
    HomeComponent,
    ReportComponent,
    ShDetails0Component,
    ShDetails1Component,
    ShDetails2Component,
    ShDetails3Component,
    SignUpComponent,
    ForgotComponent,
    MemberComponent
  ],
  imports: [
    BrowserModule,
    FormsModule,
    ReactiveFormsModule,
    HttpClientModule,
    AngularMyDatePickerModule,
    AppRoutingModule
  ],
  providers: [
    { provide: HTTP_INTERCEPTORS, useClass: JwtInterceptor, multi: true },
    { provide: HTTP_INTERCEPTORS, useClass: ErrorInterceptor, multi: true },
    { provide: 'APIURL', useValue: environment.apiUrl },
    { provide: 'ACCESSTOKEN', useValue: environment.accessToken }
  ],
  bootstrap: [AppComponent]
})
export class AppModule { }
